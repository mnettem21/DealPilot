"""Per-deal checks: merge documents into facts, find missing docs / mismatches, run the 5 old fraud tricks."""
import datetime as dt
import re
from collections import defaultdict

from .extract import parse_needed_by, purpose_category

NICK = {"bob": "robert", "rob": "robert", "bill": "william", "will": "william", "jim": "james", "jimmy": "james",
        "mike": "michael", "liz": "elizabeth", "beth": "elizabeth", "tom": "thomas", "dave": "david", "dan": "daniel",
        "joe": "joseph", "chris": "christopher", "tony": "anthony", "steve": "steven", "kate": "katherine",
        "kathy": "katherine", "pat": "patricia", "sue": "susan", "dick": "richard", "rick": "richard", "ed": "edward"}
SUFFIX = {"llc", "inc", "pllc", "co", "company", "corp", "corporation", "ltd", "lp", "the"}
ABBR = {"drive": "dr", "street": "st", "road": "rd", "boulevard": "blvd", "avenue": "ave", "lane": "ln",
        "circle": "cir", "parkway": "pkwy", "suite": "ste", "apartment": "apt", "north": "n", "south": "s",
        "east": "e", "west": "w"}
SHELL_RE = re.compile(r"\bPMB\b|\bP\.?\s?O\.?\s?Box\b|mail\s?box|\bUPS Store\b|\bpostal annex\b|\bpak mail\b", re.I)


def norm_name(s):
    toks = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    return " ".join(t for t in toks if t not in SUFFIX)


def norm_addr(s):
    toks = re.sub(r"[^a-z0-9 ]", " ", (s or "").lower()).split()
    return " ".join(ABBR.get(t, t) for t in toks)


def building(addr):
    """street number + street name, no suite/city (to spot the same building)."""
    a = norm_addr((addr or "").split(",")[0])
    a = re.sub(r"\b(ste|apt|unit|pmb)\b.*$", "", a)
    a = re.sub(r"\s#?\d+\s*$", "", a) if re.search(r"#", addr or "") else a
    return a.strip()


def first_last(name):
    t = norm_name(name).split()
    if not t:
        return ("", "")
    return (NICK.get(t[0], t[0]), t[-1])


def same_person(a, b):
    return bool(a and b) and first_last(a) == first_last(b)


def digits(s):
    return re.sub(r"\D", "", s or "")


def of_kind(docs, kind):
    return [d for d in docs if d["kind"] == kind]


def first(docs, kind):
    x = of_kind(docs, kind)
    return x[0] if x else None


def split_preparer(s):
    """'K. Delacroix, Delacroix Bookkeeping Services · 214-...' or 'K. Delacroix (Firm)' -> (name, firm)."""
    if not s:
        return None, None
    s = re.sub(r"\s*·.*$", "", s)
    m = re.match(r"^(.+?)\s*\((.+)\)$", s)
    if m:
        return m.group(1).strip(), m.group(2).strip()
    parts = [p.strip() for p in s.split(",")]
    if len(parts) >= 3 and re.fullmatch(r"(CPA|EA|CFP)", parts[1]):
        return f"{parts[0]}, {parts[1]}", ", ".join(parts[2:])
    return parts[0], ", ".join(parts[1:]) or None


def created_before_last_txn(doc):
    """PDF creation date earlier than the last transaction it lists (a statement cannot exist before its own activity)."""
    m = re.search(r"([A-Z][a-z]{2}) +(\d+) +[\d:]+ +(\d{4})", doc.get("created") or "")
    txs = (doc.get("data") or {}).get("txs") or []
    if not m or not txs:
        return False
    try:
        made = dt.datetime.strptime(f"{m.group(1)} {m.group(2)} {m.group(3)}", "%b %d %Y").date().isoformat()
    except ValueError:
        return False
    return made < max(t["date"] for t in txs)


def merge_facts(docs):
    """One flat dict of everything we know about the deal, with provenance-free simple values."""
    app = (first(docs, "application") or {}).get("data", {})
    idd = (first(docs, "guarantor_id") or {}).get("data", {})
    cert = (first(docs, "certificate_of_formation") or {}).get("data", {})
    ein = (first(docs, "ein_letter") or {}).get("data", {})
    fin = (first(docs, "financial_statements") or {}).get("data", {})
    tax = (first(docs, "tax_return") or {}).get("data", {})
    ext = (first(docs, "tax_extension") or {}).get("data", {})
    quote = (first(docs, "equipment_quote") or {}).get("data", {})
    letter = (first(docs, "request_letter") or {}).get("data", {})
    stmts = sorted([d["data"] for d in of_kind(docs, "bank_statement") if d["data"].get("month")], key=lambda s: s["month"])
    app_doc = first(docs, "application")
    pname, pfirm = split_preparer(fin.get("prepared_by") or tax.get("preparer"))
    pname2, pfirm2 = split_preparer(tax.get("preparer")) if tax.get("preparer") else (None, None)
    bank = None
    if app.get("primary_bank"):
        bank = re.sub(r"\s*\.\.\.\d+\s*$", "", app["primary_bank"]).strip()
    credits = [t["amount"] for s in stmts for t in s["txs"] if t["amount"] > 0]
    round_share = (sum(1 for c in credits if round(c) == c and int(round(c)) % 100 == 0) / len(credits)) if credits else None
    out_xfers = sorted({m.group(1) for s in stmts for t in s["txs"]
                        for m in [re.search(r"TRANSFER TO XXXX(\d+)", t["desc"])] if m})
    weekdays = sorted({dt.date.fromisoformat(t["date"]).strftime("%a") for s in stmts for t in s["txs"] if t["amount"] > 0})
    dep_months = [{"total": sum(t["amount"] for t in s["txs"] if t["amount"] > 0), "count": sum(1 for t in s["txs"] if t["amount"] > 0),
                   "round": sum(1 for t in s["txs"] if t["amount"] > 0 and round(t["amount"]) == t["amount"] and int(t["amount"]) % 100 == 0)
                   / max(1, sum(1 for t in s["txs"] if t["amount"] > 0)), "largest": max([t["amount"] for t in s["txs"]] + [0])}
                  for s in stmts if any(t["amount"] > 0 for t in s["txs"])]
    phone = (app.get("phone_email") or "").split("/")[0].strip() or None
    return {
        "legal_name": app.get("legal_name"), "ein": app.get("ein"),
        "formation_date": app.get("formation_date") or cert.get("formation_date"),
        "business_address": app.get("business_address"), "phone": phone,
        "amount_requested": app.get("amount_requested"), "product": app.get("product"),
        "purpose_category": purpose_category(app.get("use_of_proceeds"), app.get("product")),
        "use_of_proceeds": app.get("use_of_proceeds"), "term_months": app.get("term_months"),
        "guarantor_name": app.get("guarantor_name"), "guarantor_dob": app.get("guarantor_dob"),
        "guarantor_address": app.get("guarantor_address"), "guarantor_ssn4": app.get("guarantor_ssn4"),
        "guarantor_dl": app.get("guarantor_dl"), "affiliated": app.get("affiliated"),
        "annual_revenue_stated": app.get("annual_revenue_stated"),
        "needed_by": parse_needed_by(app.get("needed_by_raw")),
        "signed": app.get("signed"), "signed_date": app.get("signed_date"),
        "registered_agent": cert.get("registered_agent"), "registered_agent_address": cert.get("registered_agent_address"),
        "preparer_name": pname or pname2, "preparer_firm": pfirm or pfirm2, "preparer_phone": fin.get("preparer_phone"),
        "preparer_ptin": tax.get("ptin"),
        "bank_name": bank, "pdf_producer": (app_doc or {}).get("producer"),
        "statement_producers": sorted({d["producer"] for d in of_kind(docs, "bank_statement") if d["producer"]}),
        "vendor": quote.get("vendor"), "vendor_address": quote.get("vendor_address"),
        "vendor_phone": quote.get("vendor_phone"), "quote_total": quote.get("total"),
        "remit_acct_last4": quote.get("remit_acct_last4"), "remit_routing": quote.get("remit_routing"),
        "remit_bank": quote.get("remit_bank"),
        "outbound_transfer_accts": out_xfers, "dep_months": dep_months,
        "stmt_created_early": sum(1 for d in of_kind(docs, "bank_statement") if created_before_last_txn(d)), "round_deposit_share": round_share, "deposit_weekdays": weekdays,
        "tax_gross_receipts": tax.get("gross_receipts"), "fin_revenue_fy": fin.get("revenue_fy"),
        "fin_cash": fin.get("cash"), "id_name": idd.get("name"), "id_dob": idd.get("dob"),
        "id_address": idd.get("address"), "id_dl": idd.get("dl"),
        "letter_amount": letter.get("amount"), "has_extension": bool(ext),
        "months": [s["month"] for s in stmts],
        "app_acct_last4": (re.search(r"(\d{4})\s*$", app.get("primary_bank") or "") or [None, None])[1],
        "stmt_acct_last4": sorted({m.group(1) for s in stmts for m in [re.search(r"\.\.\.(\d{4})", s.get("account") or "")] if m}),
        "annualized_deposits": (sum(s["deposits"] or 0 for s in stmts) / len(stmts) * 12) if stmts else None,
    }


def expected_months(ref_date, n=6):
    y, m = ref_date.year, ref_date.month
    out = []
    for _ in range(n):
        m -= 1
        if m == 0:
            y, m = y - 1, 12
        out.append(f"{y}-{m:02d}")
    return sorted(out)


def business_age_months(f, ref):
    try:
        d = dt.date.fromisoformat(f["formation_date"])
        return (ref.year - d.year) * 12 + ref.month - d.month
    except Exception:
        return None


def check_deal(docs, ref_date, hist=None):
    """-> (facts, issues[list of 'KIND:target'], notes{issue: plain-words detail})"""
    f = merge_facts(docs)
    issues, notes = [], {}

    def add(issue, why):
        if issue not in issues:
            issues.append(issue)
            notes[issue] = why

    kinds = {d["kind"] for d in docs}
    # --- missing documents
    for kind, label in [("application", "application"), ("guarantor_id", "guarantor_id"),
                        ("certificate_of_formation", "certificate_of_formation"), ("ein_letter", "ein_letter"),
                        ("financial_statements", "financial_statements")]:
        if kind not in kinds:
            add(f"MISSING_DOC:{label}", f"No {label.replace('_', ' ')} found in the folder.")
    age = business_age_months(f, ref_date)
    if "tax_return" not in kinds:
        if not (f["has_extension"] and age is not None and age < 24):
            add("MISSING_DOC:tax_return", "No 2025 tax return (and no valid Form 7004 for a business under 2 years old).")
    if f["product"] == "Equipment Loan" and "equipment_quote" not in kinds:
        add("MISSING_DOC:equipment_quote", "Equipment loan but no vendor quote in the folder.")
    have = set(f["months"])
    for m in expected_months(ref_date):
        if m not in have:
            add(f"MISSING_DOC:bank_statement_{m}", f"Bank statement for {m} is missing (need the 6 most recent full months).")
    if "application" in kinds and not f["signed"]:
        add("MISSING_DOC:application", "Loan application is not signed.")
    for d in of_kind(docs, "bank_statement"):
        s = d["data"]
        if s.get("month") and s.get("pages_declared") and set(s["pages_seen"]) != set(range(1, s["pages_declared"] + 1)):
            add(f"MISSING_PAGE:bank_statement_{s['month']}",
                f"{s['month']} statement says {s['pages_declared']} pages but only pages {s['pages_seen']} are present.")

    # --- mismatches
    names = {"application": f["legal_name"]}
    for kind, key in [("certificate_of_formation", "legal_name"), ("ein_letter", "legal_name"),
                      ("financial_statements", "legal_name"), ("tax_return", "legal_name")]:
        d = first(docs, kind)
        if d:
            names[kind] = d["data"].get(key)
    for s in of_kind(docs, "bank_statement"):
        names.setdefault("bank_statement", s["data"].get("holder"))
    q = first(docs, "equipment_quote")
    if q:
        names["equipment_quote"] = q["data"].get("customer")
    base = norm_name(f["legal_name"])
    for k, v in names.items():
        if v and base and norm_name(v) != base:
            add("MISMATCH:legal_name", f"Name on the {k.replace('_', ' ')} ('{v}') differs from the application ('{f['legal_name']}').")
    eins = {"application": f["ein"]}
    for kind in ("ein_letter", "tax_return", "tax_extension"):
        d = first(docs, kind)
        if d:
            eins[kind] = d["data"].get("ein")
    for k, v in eins.items():
        if v and f["ein"] and digits(v) != digits(f["ein"]):
            add("MISMATCH:ein", f"EIN on the {k.replace('_', ' ')} ({v}) differs from the application ({f['ein']}).")
    amts = {"request letter": f["letter_amount"], "equipment quote": f["quote_total"]}
    for k, v in amts.items():
        if v and f["amount_requested"] and abs(v - f["amount_requested"]) > 1:
            add("MISMATCH:amount_requested", f"Amount on the {k} (${v:,.0f}) differs from the application (${f['amount_requested']:,.0f}).")
    if f["id_name"] and f["guarantor_name"] and not same_person(f["id_name"], f["guarantor_name"]):
        add("MISMATCH:guarantor_name", f"Guarantor on the ID is '{f['id_name']}' but the application says '{f['guarantor_name']}'.")
    if f["id_dob"] and f["guarantor_dob"] and f["id_dob"] != f["guarantor_dob"]:
        add("MISMATCH:guarantor_dob", f"Date of birth on the ID ({f['id_dob']}) differs from the application ({f['guarantor_dob']}).")
    if f["id_address"] and f["guarantor_address"] and norm_addr(f["id_address"]) != norm_addr(f["guarantor_address"]):
        add("MISMATCH:guarantor_address", f"Address on the ID ('{f['id_address']}') differs from the application ('{f['guarantor_address']}').")
    rev, tax_rev = f["annual_revenue_stated"], f["tax_gross_receipts"]
    if rev and tax_rev and abs(rev - tax_rev) / max(rev, tax_rev) > 0.10:
        add("MISMATCH:annual_revenue", f"Application revenue ${rev:,.0f} vs tax return gross receipts ${tax_rev:,.0f}.")

    # --- bank statements: balances chain + totals (STMT_TAMPER, bank_balance)
    stmts = sorted([d["data"] for d in of_kind(docs, "bank_statement") if d["data"].get("month")], key=lambda s: s["month"])
    def next_month(m):
        y, mo = int(m[:4]), int(m[5:])
        return f"{y + (mo == 12)}-{(mo % 12) + 1:02d}"

    for a, b in zip(stmts, stmts[1:]):
        if next_month(a["month"]) != b["month"]:
            continue  # a month is missing; that is a MISSING_DOC, not tampering
        if a["closing"] is not None and b["opening"] is not None and abs(a["closing"] - b["opening"]) > 0.01:
            add("OLD_TRICK:STMT_TAMPER", f"{b['month']} opening balance ${b['opening']:,.2f} != {a['month']} closing ${a['closing']:,.2f}.")
            add("MISMATCH:bank_balance", f"Balance does not carry over from {a['month']} to {b['month']}.")
    for s in stmts:
        if None not in (s["opening"], s["deposits"], s["withdrawals"], s["closing"]) and \
                abs(s["opening"] + s["deposits"] - s["withdrawals"] - s["closing"]) > 0.01:
            add("OLD_TRICK:STMT_TAMPER", f"{s['month']} summary does not add up: opening + deposits - withdrawals != closing.")
            add("MISMATCH:bank_balance", f"{s['month']} closing balance does not match its own summary.")
    for s in stmts:
        if s["format"] != "pdf" or not s["txs"] or len(s["pages_seen"]) != (s["pages_declared"] or 1):
            continue
        cr = round(sum(t["amount"] for t in s["txs"] if t["amount"] > 0), 2)
        db = round(-sum(t["amount"] for t in s["txs"] if t["amount"] < 0), 2)
        if (s["deposits"] is not None and abs(cr - s["deposits"]) > 0.01) or (s["withdrawals"] is not None and abs(db - s["withdrawals"]) > 0.01):
            add("OLD_TRICK:STMT_TAMPER", f"{s['month']} listed transactions (+{cr:,.2f}/-{db:,.2f}) do not add up to the summary.")
            add("MISMATCH:bank_balance", f"{s['month']} transactions do not add up to the statement summary.")
    if stmts and f["fin_cash"] and stmts[-1]["month"] == "2026-10" and stmts[-1]["closing"] is not None \
            and abs(stmts[-1]["closing"] - f["fin_cash"]) > 1:
        add("MISMATCH:bank_balance", f"Balance sheet cash ${f['fin_cash']:,.2f} != Oct closing ${stmts[-1]['closing']:,.2f}.")

    # --- old tricks
    ann = f["annualized_deposits"]
    if rev and ann and rev > 1.5 * ann:
        add("OLD_TRICK:REV_INFLATION", f"Stated revenue ${rev:,.0f} is more than 1.5x annualized deposits ${ann:,.0f}.")
    if any(i.startswith("MISMATCH:guarantor_") for i in issues):
        add("OLD_TRICK:ID_MISMATCH", "Name/DOB/address on the photo ID disagrees with the application.")
    if SHELL_RE.search(f["business_address"] or ""):
        add("OLD_TRICK:SHELL_ADDRESS", f"Business address '{f['business_address']}' looks like a mailbox store.")
    if hist and f["ein"]:
        others = {n for n in hist.names_for_ein(f["ein"]) if n != norm_name(f["legal_name"])}
        if others:
            add("OLD_TRICK:DUP_EIN", f"EIN {f['ein']} was used in history by a different name: {sorted(others)}.")
    return f, issues, notes


def ref_date_for(f, fallback):
    try:
        return dt.date.fromisoformat(f["signed_date"])
    except Exception:
        return fallback
