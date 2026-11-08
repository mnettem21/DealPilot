"""Read a loan folder: classify each file by CONTENT and pull out facts.

Rules first (the forms are label/value text). The local LLM (llm.py) only fills fields the rules left empty.
"""
import csv
import io
import re
import subprocess
from pathlib import Path

MONEY = re.compile(r"\$\s?([\d,]+(?:\.\d+)?)")


def money(s):
    if not s:
        return None
    m = MONEY.search(s)
    if m:
        return float(m.group(1).replace(",", ""))
    m = re.search(r"([\d,]+(?:\.\d+)?)", s)
    return float(m.group(1).replace(",", "")) if m else None


def read_file(path):
    """-> dict(text, producer, created, pages) for any supported file type."""
    p = Path(path)
    ext = p.suffix.lower()
    info = {"text": "", "producer": None, "created": None, "pages": None}
    if ext == ".pdf":
        meta = subprocess.run(["pdfinfo", str(p)], capture_output=True, text=True).stdout
        for line in meta.splitlines():
            k, _, v = line.partition(":")
            if k == "Producer":
                info["producer"] = v.strip() or None
            elif k == "CreationDate":
                info["created"] = v.strip() or None
            elif k == "Pages":
                info["pages"] = int(v)
        info["text"] = subprocess.run(["pdftotext", "-layout", str(p), "-"], capture_output=True, text=True).stdout
        if len(info["text"].strip()) < 20:  # scanned image PDF -> OCR
            info["text"] = _ocr_pdf(p)
    elif ext in (".jpg", ".jpeg", ".png"):
        info["text"] = subprocess.run(["tesseract", str(p), "-"], capture_output=True, text=True).stdout
    else:
        info["text"] = p.read_text(errors="replace")
    return info


def _ocr_pdf(p):
    out = []
    tmp = Path("/tmp") / f"dp_ocr_{p.stem}"
    subprocess.run(["pdftoppm", "-r", "200", "-png", str(p), str(tmp)], capture_output=True)
    for img in sorted(Path("/tmp").glob(f"dp_ocr_{p.stem}*.png")):
        out.append(subprocess.run(["tesseract", str(img), "-"], capture_output=True, text=True).stdout)
        img.unlink()
    return "\n".join(out)


def classify(text, path=""):
    t = re.sub(r"\s+", " ", text.lower())
    if "posted date" in t[:300] and "running bal" in t[:300]:
        return "bank_statement"
    if "business checking statement" in t:
        return "bank_statement"
    if "small business loan application" in t:
        return "application"
    if "driver license" in t or "driver's license" in t:
        return "guarantor_id"
    if "certificate of formation" in t:
        return "certificate_of_formation"
    if "cp 575" in t or "ein assigned" in t:
        return "ein_letter"
    if "form 7004" in t:
        return "tax_extension"
    if "financial statements" in t and ("profit & loss" in t or "balance sheet" in t):
        return "financial_statements"
    if "income tax return" in t or "gross receipts" in t:
        return "tax_return"
    if "quotation" in t and "remit to" in t:
        return "equipment_quote"
    if "contract" in t and "summary" in t:
        return "contract_summary"
    if "re: loan request" in t or "requesting $" in t:
        return "request_letter"
    return "other"


def kv(text):
    """'Label     value' lines (two+ spaces between) -> {label_lower: value}. First occurrence wins."""
    out = {}
    for line in text.splitlines():
        m = re.match(r"^\s*([A-Za-z][A-Za-z /&()\-\.']*?)\s{2,}(\S.*?)\s*$", line)
        if m:
            out.setdefault(m.group(1).strip().lower(), m.group(2).strip())
    return out


# ---------- per-document parsers ----------

def parse_application(text):
    f = kv(text)
    sig = re.search(r"/s/\s*(.+?)\s+Date:\s*(\d{4}-\d{2}-\d{2})", text)
    needed = f.get("funds needed by", "")
    return {
        "legal_name": f.get("legal business name"),
        "ein": f.get("federal ein"),
        "formation_date": f.get("date business established"),
        "business_address": f.get("business address"),
        "phone_email": f.get("business phone / email"),
        "industry": f.get("industry (naics)"),
        "revenue_raw": f.get("annual revenue (most recent year)"),
        "annual_revenue_stated": revenue_value(f.get("annual revenue (most recent year)")),
        "primary_bank": f.get("primary bank / account"),
        "amount_requested": money(f.get("amount requested")),
        "product": f.get("product"),
        "use_of_proceeds": f.get("use of proceeds"),
        "term_months": int(re.search(r"\d+", f["requested term"]).group()) if f.get("requested term") else None,
        "needed_by_raw": needed,
        "guarantor_name": f.get("name"),
        "guarantor_dob": f.get("date of birth"),
        "guarantor_ssn4": (re.search(r"(\d{4})\s*$", f.get("ssn", "")) or [None, None])[1],
        "guarantor_address": f.get("home address"),
        "guarantor_dl": re.sub(r"^TX\s*", "", f.get("driver license", "")) or None,
        "affiliated": f.get("affiliated businesses"),
        "signed": bool(sig),
        "signed_date": sig.group(2) if sig else None,
    }


def revenue_value(s):
    if not s:
        return None
    rng = re.search(r"(\d[\d,]*)\s*-\s*(\d[\d,]*)\s*k", s, re.I)  # "approx 400-500k"
    if rng:
        return (float(rng.group(1).replace(",", "")) + float(rng.group(2).replace(",", ""))) / 2 * 1000
    return money(s)


def parse_id(text):
    f = kv(text)
    if "name" in f:
        dl = re.sub(r"\D", "", f.get("dl number", ""))
        return {"name": f.get("name"), "address": f.get("address"), "dob": f.get("date of birth"), "dl": dl or None}
    # OCR'd photo: free text
    lines = [l.strip() for l in text.splitlines() if l.strip()]
    dl = re.search(r"DL\s*(\d{6,9})", text)
    dob = re.search(r"DOB\s*(\d{4}-\d{2}-\d{2})", text)
    name = next((l for l in lines if re.fullmatch(r"[A-Z][A-Z .'\-]+", l) and "TEXAS" not in l and "LICENSE" not in l), None)
    addr_i = next((i for i, l in enumerate(lines) if re.match(r"\d+ ", l)), None)
    addr = " ".join(lines[addr_i:addr_i + 2]) if addr_i is not None else None
    return {"name": name.title() if name else None, "address": addr, "dob": dob.group(1) if dob else None,
            "dl": dl.group(1) if dl else None}


def parse_cert(text):
    f = kv(text)
    return {"legal_name": f.get("entity name"), "formation_date": f.get("effective date"), "address": f.get("principal office"),
            "registered_agent": f.get("registered agent"), "registered_agent_address": f.get("registered office"),
            "organizer": f.get("organizer")}


def parse_ein_letter(text):
    f = kv(text)
    return {"legal_name": f.get("name of business"), "ein": f.get("employer identification number") or f.get("ein")}


def parse_fin(text):
    first = next((l.strip() for l in text.splitlines() if l.strip()), "")
    prep = re.search(r"Prepared by:\s*(.+)", text)
    rev = re.search(r"Revenue\s+\$([\d,\.]+)(?:\s+\$([\d,\.]+))?", text)
    cash = re.search(r"Cash\s+\$([\d,\.]+)", text)
    name = re.search(r"^\s*(.+?)\s+—\s+Financial Statements", text, re.M)
    prep_s = prep.group(1).strip() if prep else None
    phone = re.search(r"(\d{3}-\d{3}-\d{4})", prep_s or "")
    return {"legal_name": name.group(1).strip() if name else None, "prepared_by": prep_s,
            "preparer_phone": phone.group(1) if phone else None,
            "revenue_fy": money(rev.group(1)) if rev else None,
            "revenue_ytd": money(rev.group(2)) if rev and rev.group(2) else None,
            "cash": money(cash.group(1)) if cash else None, "firm_header": first}


def parse_tax(text):
    f = kv(text)
    return {"legal_name": f.get("name"), "ein": f.get("ein"), "gross_receipts": money(f.get("gross receipts or sales")),
            "ordinary_income": money(f.get("ordinary business income")), "preparer": f.get("paid preparer"),
            "ptin": f.get("preparer ptin"), "filed": f.get("date filed")}


def parse_ext(text):
    f = kv(text)
    return {"legal_name": f.get("name"), "ein": f.get("ein"), "status": f.get("status")}


def parse_quote(text):
    f = kv(text)
    vend = re.search(r"^\s*(.+?)\s+·", text, re.M)
    remit = re.search(r"Remit to:\s*(.+?)\s+·\s*Routing\s+(\d+)\s+·\s*Account ending\s+(\d+)", text)
    total = re.search(r"TOTAL\s+\$([\d,\.]+)", text)
    ph = re.search(r"(\d{3}-\d{3}-\d{4})", text)
    addr = re.search(r"·\s*(\d+[^·]+?TX \d{5})", text)
    return {"vendor": vend.group(1).strip() if vend else None, "vendor_address": addr.group(1).strip() if addr else None,
            "vendor_phone": ph.group(1) if ph else None, "customer": f.get("customer"), "quote_date": f.get("quote date"),
            "total": money(total.group(1)) if total else None,
            "remit_bank": remit.group(1) if remit else None, "remit_routing": remit.group(2) if remit else None,
            "remit_acct_last4": remit.group(3) if remit else None}


def parse_letter(text):
    amt = re.search(r"requesting\s+\$([\d,\.]+)", text)
    term = re.search(r"(\d+)-month", text)
    sig = re.findall(r"^\s*(.+?),\s*(.+?),\s*(.+)$", text, re.M)
    return {"amount": money(amt.group(1)) if amt else None, "term_months": int(term.group(1)) if term else None,
            "signer": sig[-1][0] if sig else None}


def parse_statement(text, pdf_pages=None):
    """PDF or CSV statement -> period, balances, transactions, page info."""
    if "posted date" in text[:300].lower():
        return _parse_csv_statement(text)
    f = kv(text)
    per = re.search(r"Statement period\s+(\d{4}-\d{2})-\d{2}\s+to\s+(\d{4}-\d{2}-\d{2})", text)
    opening = money(f.get("opening balance"))
    txs, prev = [], opening
    for line in text.splitlines():
        m = re.match(r"^\s*(\d\d)/(\d\d)/(\d{4})\s+(.+?)\s{2,}((?:\$[\d,]+\.\d\d\s*)+)$", line)
        if not m:
            continue
        amts = [float(a.replace(",", "")) for a in re.findall(r"\$([\d,]+\.\d\d)", m.group(5))]
        bal, amt = amts[-1], (amts[-2] if len(amts) > 1 else 0.0)
        delta = round(bal - prev, 2) if prev is not None else amt
        txs.append({"date": f"{m.group(3)}-{m.group(1)}-{m.group(2)}", "desc": m.group(4).strip(),
                    "amount": delta, "balance": bal})
        prev = bal
    pages = [(int(a), int(b)) for a, b in re.findall(r"Page\s+(\d+)\s+of\s+(\d+)", text)]
    return {"month": per.group(1) if per else None, "holder": f.get("account holder"), "account": f.get("account"),
            "opening": opening, "deposits": money(f.get("total deposits & credits")),
            "withdrawals": money(f.get("total withdrawals & debits")), "closing": money(f.get("closing balance")),
            "txs": txs, "pages_seen": sorted({a for a, _ in pages}),
            "pages_declared": max((b for _, b in pages), default=None), "pdf_pages": pdf_pages, "format": "pdf"}


def _parse_csv_statement(text):
    lines = text.strip().splitlines()
    head = lines[0]
    rows = list(csv.DictReader(io.StringIO("\n".join(lines[1:]))))
    txs = []
    for r in rows:
        m, d, y = r["Posted Date"].split("/")
        txs.append({"date": f"20{y}-{int(m):02d}-{int(d):02d}", "desc": r["Details"], "amount": float(r["Amount"]),
                    "balance": float(r["Running Bal"])})
    opening = round(txs[0]["balance"] - txs[0]["amount"], 2) if txs else None
    dep = round(sum(t["amount"] for t in txs if t["amount"] > 0), 2)
    wd = round(-sum(t["amount"] for t in txs if t["amount"] < 0), 2)
    holder = head.split(" - ")[-1].strip() if " - " in head else None
    return {"month": txs[0]["date"][:7] if txs else None, "holder": holder, "account": head,
            "opening": opening, "deposits": dep, "withdrawals": wd, "closing": txs[-1]["balance"] if txs else None,
            "txs": txs, "pages_seen": [1], "pages_declared": 1, "pdf_pages": None, "format": "csv"}


PARSERS = {"application": parse_application, "guarantor_id": parse_id, "certificate_of_formation": parse_cert,
           "ein_letter": parse_ein_letter, "financial_statements": parse_fin, "tax_return": parse_tax,
           "tax_extension": parse_ext, "equipment_quote": parse_quote, "request_letter": parse_letter}


# ---------- folder-level ----------

def read_folder(folder, only=None):
    """Read every (arrived) file in a deal folder. `only` = set of relative file names allowed (sim inbox)."""
    folder = Path(folder)
    docs = []
    for p in sorted(folder.iterdir()):
        if p.name.startswith(".") or not p.is_file():
            continue
        if only is not None and p.name not in only:
            continue
        info = read_file(p)
        kind = classify(info["text"], p)
        if kind == "bank_statement":
            data = parse_statement(info["text"], info["pages"])
        elif kind in PARSERS:
            data = PARSERS[kind](info["text"])
        else:
            data = {}
        docs.append({"file": p.name, "kind": kind, "producer": info["producer"], "created": info["created"],
                     "pages": info["pages"], "data": data, "text": info["text"]})
    return docs


PURPOSE_KEYWORDS = [
    ("payroll", ["payroll"]),
    ("refinance", ["refinanc", "pay off", "payoff"]),
    ("equipment", ["equipment", "truck", "trailer", "tractor", "mower", "lift", "printer", "machine", "x-ray", "imaging", "van ", "oven", "grinder"]),
    ("inventory", ["inventory", "stock up", "stock "]),
    ("expansion", ["build out", "second location", "expan", "new location", "hire", "open "]),
    ("working_capital", ["working capital", "bridge", "receivable", "cash flow", "operating"]),
]


def purpose_category(use, product=None):
    u = (use or "").lower()
    for cat, kws in PURPOSE_KEYWORDS:
        if any(re.search(r"\b" + re.escape(k), u) for k in kws):
            return cat
    return {"Equipment Loan": "equipment", "Working Capital Loan": "working_capital"}.get(product, "working_capital")


def parse_needed_by(raw):
    """'Friday 2026-11-06 by 3:00 PM (payroll)' -> '2026-11-06T15:00-06:00'; no date -> None."""
    m = re.search(r"(\d{4}-\d{2}-\d{2})", raw or "")
    if not m:
        return None
    t = re.search(r"(\d{1,2}):(\d{2})\s*(AM|PM)", raw, re.I)
    h, mi = (int(t.group(1)), int(t.group(2))) if t else (17, 0)
    if t and t.group(3).upper() == "PM" and h < 12:
        h += 12
    return f"{m.group(1)}T{h:02d}:{mi:02d}-06:00"
