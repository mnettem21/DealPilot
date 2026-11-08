import csv
import hashlib
import io
import re
from pathlib import Path

try:
    from pypdf import PdfReader
except ImportError:  # pragma: no cover - fallback to PyPDF2 if installed
    try:
        from PyPDF2 import PdfReader
    except ImportError:  # pragma: no cover - clear runtime error is returned by process_document
        PdfReader = None


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


def process_document(path: Path):
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        text, pages, producer = extract_pdf(path)
    elif suffix == ".csv":
        text, _rows, _columns = extract_csv(path)
        pages, producer = 1, "CSV export"
    elif suffix in {".jpg", ".jpeg", ".png"}:
        text, pages, producer = "", 1, "image upload"
    else:
        raise ValueError(f"Unsupported file type: {suffix}")

    document_type = classify(text, path.name)
    facts = extract_facts(document_type, text, path.name)
    return {
        "checksum": checksum(path),
        "document_type": document_type,
        "status": "PROCESSED",
        "page_count": pages,
        "producer": producer,
        "text": text,
        "facts": facts,
    }

