import base64
import csv
import hashlib
import io
import json
import os
import re
from pathlib import Path

from services.common.env import ROOT, load_env
from services.parser import llm

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - fallback to PyPDF2 if installed
    try:
        from PyPDF2 import PdfReader
    except ImportError:  # pragma: no cover - clear runtime error is returned by process_document
        PdfReader = None


DOCUMENT_TYPES = frozenset(
    {
        "loan_application",
        "guarantor_id",
        "certificate_of_formation",
        "ein_letter",
        "bank_statement",
        "financial_statements",
        "tax_return",
        "tax_extension",
        "equipment_quote",
        "request_letter",
        "contract_summary",
        "other",
    }
)

PURPOSE_CATEGORIES = frozenset(
    {"working_capital", "equipment", "inventory", "payroll", "expansion", "refinance"}
)

MONEY_FIELDS = frozenset(
    {
        "amount_requested",
        "annual_revenue_stated",
        "opening_balance",
        "total_deposits",
        "closing_balance",
        "quote_total",
    }
)

DATE_FIELDS = frozenset(
    {"formation_date", "formation_date_support", "guarantor_dob", "needed_by"}
)

FACT_KEYS = {
    "loan_application": {
        "legal_name",
        "ein",
        "formation_date",
        "business_address",
        "amount_requested",
        "product",
        "purpose",
        "purpose_category",
        "term_months",
        "guarantor_name",
        "guarantor_dob",
        "guarantor_dl_number",
        "annual_revenue_stated",
        "annual_revenue_stated_text",
        "needed_by",
    },
    "guarantor_id": {"id_name", "id_dl_number"},
    "certificate_of_formation": {
        "formation_legal_name",
        "formation_date_support",
        "formation_address",
        "registered_agent",
        "registered_agent_address",
    },
    "ein_letter": {"ein_legal_name", "ein_support"},
    "bank_statement": {
        "statement_month",
        "opening_balance",
        "total_deposits",
        "closing_balance",
        "declared_pages",
        "seen_page_markers",
        "transfer_accounts",
    },
    "financial_statements": {"preparer"},
    "tax_return": {"preparer"},
    "tax_extension": set(),
    "equipment_quote": {"equipment_vendor", "vendor_remit_acct_last4", "quote_total"},
    "request_letter": set(),
    "contract_summary": set(),
    "other": set(),
}

SYSTEM_PROMPT = """You extract facts from one loan-file document for a bank intake and credit review.
Reply with one JSON object and no other text.
Use null for any value the document does not state. Do not guess or calculate a number the document does not give.
If annual revenue is a range or an approximation, set annual_revenue_stated to null and put the phrase in annual_revenue_stated_text.
document_type must be one of: loan_application, guarantor_id, certificate_of_formation, ein_letter, bank_statement, financial_statements, tax_return, tax_extension, equipment_quote, request_letter, contract_summary, other.
Include these keys: document_type, legal_name, ein, formation_date, business_address, amount_requested, product, purpose, purpose_category, term_months, guarantor_name, guarantor_dob, guarantor_dl_number, annual_revenue_stated, annual_revenue_stated_text, needed_by, formation_legal_name, formation_date_support, formation_address, registered_agent, registered_agent_address, ein_legal_name, ein_support, id_name, id_dl_number, statement_month, opening_balance, total_deposits, closing_balance, preparer, equipment_vendor, vendor_remit_acct_last4, quote_total.
purpose_category is one of working_capital, equipment, inventory, payroll, expansion, refinance, or null.
statement_month is YYYY-MM or null.
Dates are YYYY-MM-DD. Keep a time when the document states one, as YYYY-MM-DDTHH:MM.
Money is a number of US dollars without a dollar sign.
EIN values may keep their printed hyphens. Driver license numbers are the license number only.
"""


DOC_HINTS = [
    ("loan_application", ("small business loan application", "loan application")),
    ("guarantor_id", ("driver license", "identification card")),
    ("certificate_of_formation", ("certificate of formation", "filing acknowledgment")),
    ("ein_letter", ("cp 575", "ein assigned", "employer identification number")),
    ("financial_statements", ("profit & loss", "balance sheet")),
    ("tax_return", ("tax return", "tax year 2025", "gross receipts or sales")),
    ("tax_extension", ("form 7004", "extension of time")),
    ("request_letter", ("re: loan request", "we are requesting")),
    ("equipment_quote", ("quotation", "remit to:", "equipment quote")),
    ("bank_statement", ("business checking statement", "statement period")),
    ("contract_summary", ("contract summary",)),
]


def checksum(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def clean(value):
    return re.sub(r"\s+", " ", value).strip() if value else None


def money(value):
    if value is None:
        return None
    try:
        return float(value.replace("$", "").replace(",", "").strip())
    except (ValueError, AttributeError):
        return None


def match_after(text: str, label: str):
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    wanted = label.lower()
    for index, line in enumerate(lines[:-1]):
        if line.lower() == wanted:
            return lines[index + 1]
    return None


def classify(text: str, filename: str) -> str:
    lower = text.lower()
    for document_type, hints in DOC_HINTS:
        if any(hint in lower for hint in hints):
            return document_type

    name = filename.lower()
    fallback = {
        "application": "loan_application",
        "loan app": "loan_application",
        "guarantor": "guarantor_id",
        "license": "guarantor_id",
        "formation": "certificate_of_formation",
        "state filing": "certificate_of_formation",
        "ein": "ein_letter",
        "financial": "financial_statements",
        "p&l": "financial_statements",
        "tax": "tax_return",
        "7004": "tax_extension",
        "request": "request_letter",
        "letter to bank": "request_letter",
        "quote": "equipment_quote",
        "statement": "bank_statement",
        "export_": "bank_statement",
    }
    for hint, document_type in fallback.items():
        if hint in name:
            return document_type
    if Path(filename).suffix.lower() in {".jpg", ".jpeg", ".png"}:
        return "guarantor_id"
    return "other"


def extract_pdf(path: Path):
    if PdfReader is None:
        raise RuntimeError("pypdf or PyPDF2 is required. Install dependencies from requirements.txt")
    reader = PdfReader(path)
    text = "\n".join(page.extract_text() or "" for page in reader.pages)
    metadata = reader.metadata or {}
    return text, len(reader.pages), str(metadata.get("/Producer") or "")


def extract_csv(path: Path):
    raw = path.read_text(errors="replace")
    reader = csv.DictReader(io.StringIO(raw))
    rows = list(reader)
    columns = reader.fieldnames or []
    return raw, rows, columns


def extract_facts(document_type: str, text: str, filename: str):
    facts = {}
    if document_type == "loan_application":
        mappings = {
            "legal_name": "Legal business name",
            "dba": "DBA / trade name",
            "ein": "Federal EIN",
            "formation_date": "Date business established",
            "business_address": "Business address",
            "annual_revenue_stated": "Annual revenue (most recent year)",
            "bank_account": "Primary bank / account",
            "amount_requested": "Amount requested",
            "product": "Product",
            "purpose": "Use of proceeds",
            "term": "Requested term",
            "needed_by": "Funds needed by",
            "guarantor_name": "Name",
            "guarantor_dob": "Date of birth",
            "guarantor_address": "Home address",
            "guarantor_dl_number": "Driver license",
        }
        for field, label in mappings.items():
            facts[field] = clean(match_after(text, label))
        facts["amount_requested"] = money(facts.get("amount_requested"))
        facts["annual_revenue_stated"] = money(facts.get("annual_revenue_stated"))
        term = facts.get("term") or ""
        term_match = re.search(r"(\d+)\s*months?", term, re.I)
        facts["term_months"] = int(term_match.group(1)) if term_match else None
        dl = facts.get("guarantor_dl_number") or ""
        facts["guarantor_dl_number"] = re.sub(r"\D", "", dl) or None
        product = facts.get("product") or ""
        purpose = (facts.get("purpose") or "").lower()
        if "equipment" in product.lower() or "equipment" in purpose:
            facts["purpose_category"] = "equipment"
        elif "payroll" in purpose:
            facts["purpose_category"] = "payroll"
        elif "inventory" in purpose:
            facts["purpose_category"] = "inventory"
        elif "refinanc" in purpose:
            facts["purpose_category"] = "refinance"
        elif "expand" in purpose or "build out" in purpose:
            facts["purpose_category"] = "expansion"
        else:
            facts["purpose_category"] = "working_capital"

    elif document_type == "guarantor_id":
        facts = {
            "id_name": clean(match_after(text, "Name")),
            "id_address": clean(match_after(text, "Address")),
            "id_dob": clean(match_after(text, "Date of birth")),
            "id_dl_number": clean(match_after(text, "DL number")),
        }
        if facts.get("id_dl_number"):
            facts["id_dl_number"] = re.sub(r"\D", "", facts["id_dl_number"])

    elif document_type == "certificate_of_formation":
        facts = {
            "formation_legal_name": clean(match_after(text, "Entity name")),
            "formation_date_support": clean(match_after(text, "Effective date")),
            "formation_address": clean(match_after(text, "Principal office")),
            "registered_agent": clean(match_after(text, "Registered agent")),
            "registered_agent_address": clean(match_after(text, "Registered office")),
        }

    elif document_type == "ein_letter":
        facts = {
            "ein_legal_name": clean(match_after(text, "Name of business")),
            "ein_support": clean(match_after(text, "Employer Identification Number")),
        }

    elif document_type in {"financial_statements", "tax_return"}:
        prepared = re.search(r"Prepared by:\s*([^·\n]+)(?:·\s*([\d-]+))?", text, re.I)
        paid = re.search(r"Paid preparer\s*\n?([^\n]+)", text, re.I)
        ptin = re.search(r"Preparer PTIN\s*\n?([A-Z0-9]+)", text, re.I)
        facts = {
            "preparer": clean(prepared.group(1) if prepared else paid.group(1) if paid else None),
            "preparer_phone": clean(prepared.group(2) if prepared else None),
            "preparer_ptin": clean(ptin.group(1) if ptin else None),
        }

    elif document_type == "equipment_quote":
        vendor = next((line.strip() for line in text.splitlines() if " LLC " in line or " Inc. " in line), None)
        account = re.search(r"Account ending\s+(\d{4})", text, re.I)
        routing = re.search(r"Routing\s+(\d+)", text, re.I)
        total = re.search(r"TOTAL\s*\n?\$?([\d,]+(?:\.\d{2})?)", text, re.I)
        facts = {
            "equipment_vendor": clean(vendor.split("·")[0] if vendor else None),
            "vendor_remit_acct_last4": account.group(1) if account else None,
            "vendor_routing": routing.group(1) if routing else None,
            "quote_total": money(total.group(1)) if total else None,
        }

    elif document_type == "bank_statement":
        period = re.search(r"Statement period\s*\n?([0-9-]+)\s+to\s+([0-9-]+)", text, re.I)
        transfer_accounts = sorted(set(re.findall(r"TRANSFER TO X+([0-9]{4})", text, re.I)))
        facts = {
            "statement_month": period.group(1)[:7] if period else infer_month(filename),
            "opening_balance": money(match_after(text, "Opening balance")),
            "total_deposits": money(match_after(text, "Total deposits & credits")),
            "closing_balance": money(match_after(text, "Closing balance")),
            "transfer_accounts": transfer_accounts,
        }
        page_markers = [(int(a), int(b)) for a, b in re.findall(r"Page\s+(\d+)\s+of\s+(\d+)", text, re.I)]
        if page_markers:
            facts["declared_pages"] = max(total for _, total in page_markers)
            facts["seen_page_markers"] = sorted(set(page for page, _ in page_markers))

    return {key: value for key, value in facts.items() if value not in (None, "", [])}


def infer_month(filename: str):
    lower = filename.lower()
    month_names = {
        "may": "2026-05", "june": "2026-06", "jun": "2026-06",
        "july": "2026-07", "jul": "2026-07", "august": "2026-08",
        "aug": "2026-08", "september": "2026-09", "sep": "2026-09",
        "october": "2026-10", "oct": "2026-10",
    }
    match = re.search(r"2026[-_](\d{2})", lower)
    if match:
        return f"2026-{match.group(1)}"
    return next((value for name, value in month_names.items() if name in lower), None)


def drop_empty(facts: dict) -> dict:
    return {key: value for key, value in facts.items() if value not in (None, "", [])}


def as_money(value):
    if value is None or value == "":
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value)
    return money(str(value))


def looks_like_range(value) -> bool:
    if not isinstance(value, str):
        return False
    text = value.lower()
    if any(word in text for word in ("approx", "about", "around", "between", "~")):
        return True
    return bool(re.search(r"\d\s*[-–to]+\s*\d", text))


def as_ein(value):
    if value is None or value == "":
        return None
    digits = re.sub(r"\D", "", str(value))
    if len(digits) == 9:
        return f"{digits[:2]}-{digits[2:]}"
    return clean(str(value))


def as_digits(value):
    if value is None or value == "":
        return None
    digits = re.sub(r"\D", "", str(value))
    return digits or None


def as_date(value):
    if value is None or value == "":
        return None
    text = str(value).strip()
    match = re.match(r"(\d{4}-\d{2}-\d{2})([T ]\d{2}:\d{2}(?::\d{2})?)?$", text)
    if match:
        return match.group(1) + (match.group(2) or "").replace(" ", "T")
    return clean(text)


def as_month(value):
    if value is None or value == "":
        return None
    match = re.search(r"(20\d{2})[-/](\d{1,2})", str(value))
    if not match:
        return None
    month = int(match.group(2))
    if month < 1 or month > 12:
        return None
    return f"{match.group(1)}-{month:02d}"


def as_int(value):
    if value is None or value == "" or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return int(value)
    match = re.search(r"\d+", str(value))
    return int(match.group(0)) if match else None


def last4(value):
    digits = as_digits(value)
    if digits and len(digits) >= 4:
        return digits[-4:]
    return None


def purpose_category(product, purpose, suggested):
    product_text = (product or "").lower()
    purpose_text = (purpose or "").lower()
    if isinstance(suggested, str):
        suggested = suggested.strip().lower()
    else:
        suggested = None
    if "equipment" in product_text or "equipment" in purpose_text:
        return "equipment"
    if "payroll" in purpose_text:
        return "payroll"
    if "inventory" in purpose_text:
        return "inventory"
    if "refinanc" in purpose_text:
        return "refinance"
    if "expand" in purpose_text or "build out" in purpose_text:
        return "expansion"
    if suggested in PURPOSE_CATEGORIES:
        return suggested
    if purpose_text or product_text:
        return "working_capital"
    return None


def statement_markers(text: str) -> dict:
    facts = {}
    found = re.findall(r"Page\s+(\d+)\s+of\s+(\d+)", text or "", re.I)
    page_markers = [(int(page), int(total)) for page, total in found]
    if page_markers:
        facts["declared_pages"] = max(total for _, total in page_markers)
        facts["seen_page_markers"] = sorted({page for page, _ in page_markers})
    transfers = sorted(set(re.findall(r"TRANSFER TO X+([0-9]{4})", text or "", re.I)))
    if transfers:
        facts["transfer_accounts"] = transfers
    return facts


def normalize_model(raw: dict, text: str, filename: str):
    if not isinstance(raw, dict):
        raise ValueError("model JSON was not an object")
    document_type = str(raw.get("document_type") or "").strip().lower()
    if document_type not in DOCUMENT_TYPES:
        raise ValueError(f"document type {document_type!r} is outside the enum")

    revenue_text = raw.get("annual_revenue_stated_text")
    revenue_value = raw.get("annual_revenue_stated")
    ranged = looks_like_range(revenue_value) or looks_like_range(revenue_text)
    values = {}
    for key in FACT_KEYS[document_type]:
        if key in {"purpose_category", "declared_pages", "seen_page_markers", "transfer_accounts"}:
            continue
        value = raw.get(key)
        if key in MONEY_FIELDS:
            value = None if key == "annual_revenue_stated" and ranged else as_money(value)
        elif key in DATE_FIELDS:
            value = as_date(value)
        elif key in {"ein", "ein_support"}:
            value = as_ein(value)
        elif key in {"guarantor_dl_number", "id_dl_number"}:
            value = as_digits(value)
        elif key == "term_months":
            value = as_int(value)
        elif key == "statement_month":
            value = as_month(value)
        elif key == "vendor_remit_acct_last4":
            value = last4(value)
        elif key == "annual_revenue_stated_text":
            source = revenue_text if revenue_text not in (None, "") else revenue_value
            value = clean(str(source)) if ranged and source not in (None, "") else None
        elif isinstance(value, str):
            value = clean(value)
        values[key] = value

    if document_type == "loan_application":
        values["purpose_category"] = purpose_category(
            values.get("product"), values.get("purpose"), raw.get("purpose_category")
        )
    if document_type == "bank_statement":
        values["statement_month"] = values.get("statement_month") or infer_month(filename)
        values.update(statement_markers(text))
    return document_type, drop_empty(values)


def cache_dir() -> Path:
    load_env()
    override = os.environ.get("EXTRACT_CACHE_DIR")
    if override:
        return Path(override)
    return ROOT / "output" / "extract_cache"


def read_cache(digest: str):
    path = cache_dir() / f"{digest}.json"
    if not path.exists():
        return None
    try:
        data = json.loads(path.read_text())
    except (OSError, json.JSONDecodeError):
        return None
    if data.get("document_type") not in DOCUMENT_TYPES or not isinstance(data.get("facts"), dict):
        return None
    return data


def write_cache(digest: str, document_type: str, facts: dict) -> None:
    try:
        directory = cache_dir()
        directory.mkdir(parents=True, exist_ok=True)
        payload = {"document_type": document_type, "facts": facts}
        (directory / f"{digest}.json").write_text(json.dumps(payload, indent=2))
    except OSError as exc:
        print(f"extractor: cache write failed: {exc}")


def image_mime(suffix: str) -> str:
    if suffix == ".png":
        return "image/png"
    return "image/jpeg"


def build_messages(filename: str, producer: str, pages: int, text: str, image: bytes | None, mime: str | None):
    header = f"Filename: {filename}\nProducer: {producer}\nPage count: {pages}\n"
    if image is not None:
        encoded = base64.b64encode(image).decode()
        user = {
            "role": "user",
            "content": [
                {"type": "text", "text": header + "Read the attached image. There is no text layer."},
                {"type": "image_url", "image_url": {"url": f"data:{mime};base64,{encoded}"}},
            ],
        }
    else:
        body = text if len(text) <= 100_000 else text[:100_000] + "\n[text truncated]"
        user = {"role": "user", "content": header + "\nDocument text:\n" + body}
    return [{"role": "system", "content": SYSTEM_PROMPT}, user]


def read_document(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, pages, producer = extract_pdf(path)
        return text, pages, producer, None, None
    if suffix == ".csv":
        text, _rows, _columns = extract_csv(path)
        return text, 1, "CSV export", None, None
    if suffix in {".jpg", ".jpeg", ".png"}:
        return "", 1, "image upload", path.read_bytes(), image_mime(suffix)
    raise ValueError(f"Unsupported file type: {suffix}")


def label_fallback(text: str, filename: str):
    document_type = classify(text, filename)
    return document_type, extract_facts(document_type, text, filename)


def process_document(path: Path):
    path = Path(path)
    text, pages, producer, image, mime = read_document(path)
    digest = checksum(path)
    cached = read_cache(digest)
    if cached:
        document_type = cached["document_type"]
        facts = cached["facts"]
    else:
        try:
            raw = llm.complete_json(build_messages(path.name, producer, pages, text, image, mime))
            document_type, facts = normalize_model(raw, text, path.name)
        except Exception as exc:
            print(f"extractor: model failed for {path.name}: {exc}; using label matcher")
            document_type, facts = label_fallback(text, path.name)
        else:
            write_cache(digest, document_type, facts)
    return {
        "checksum": digest,
        "document_type": document_type,
        "status": "PROCESSED",
        "page_count": pages,
        "producer": producer,
        "text": text,
        "facts": facts,
    }

