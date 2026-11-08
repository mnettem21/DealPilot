"""Check a finished run: the submission is well-formed (SUBMISSION_FORMAT.md), obeys the safety invariants, and, when an
answer key is given, matches it. With no key (a hidden set) only the first two run.

Answer key (json): {"deals": {"<folder>": {"fraud": true|false, "reached": "Closed"|"declined"|<stage>,
                                            "issues_include": ["MISMATCH:ein"]}}, "history_flags": ["H-..."]}
Deals missing from the key are not judged. history_flags in the key must all be found; extra flags are reported."""
import re

STAGES = ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"]
REQ_TYPES = {"credit_decision", "committee_approval", "commitment_signoff", "funding_release", "fraud_hold", "fraud_release", "info_request"}
PURPOSE = {"working_capital", "equipment", "inventory", "payroll", "expansion", "refinance"}
PRODUCT = {"Term Loan", "Equipment Loan", "Revolving Line of Credit", "Working Capital Loan"}
DOCS = {"application", "guarantor_id", "certificate_of_formation", "ein_letter", "financial_statements", "tax_return", "equipment_quote"}
FIELDS = {"legal_name", "ein", "amount_requested", "guarantor_name", "guarantor_dob", "guarantor_address", "annual_revenue", "bank_balance"}


def _issue_ok(i):
    kind, _, t = i.partition(":")
    if kind in ("MISSING_DOC", "MISSING_PAGE"):
        return t in DOCS or re.fullmatch(r"bank_statement_\d{4}-\d{2}", t)
    return (kind == "MISMATCH" and t in FIELDS) or (kind == "OLD_TRICK" and bool(t))


def verify(sub, data_dir, key=None):
    bad = []
    deals = sub.get("deals", {})
    folders = sorted(p.name for p in (data_dir / "shared_folder").iterdir() if p.is_dir())
    for f in folders:
        if f not in deals:
            bad.append(f"{f}: missing from submission")
    known = set(folders)
    for n, d in deals.items():
        ex = d.get("extracted", {})
        if ex.get("purpose_category") not in PURPOSE | {None}:
            bad.append(f"{n}: bad purpose_category {ex.get('purpose_category')!r}")
        if ex.get("product") not in PRODUCT | {None}:
            bad.append(f"{n}: bad product {ex.get('product')!r}")
        bad += [f"{n}: malformed issue {i!r}" for i in d.get("issues", []) if not _issue_ok(i)]
        log = d.get("stage_log", [])
        bad += [f"{n}: unknown stage {e.get('stage')!r}" for e in log if e.get("stage") not in STAGES]
        bad += [f"{n}: bad request type {r.get('type')!r}" for r in d.get("human_requests", []) if r.get("type") not in REQ_TYPES]
        bad += [f"{n}: bad alert level {a.get('level')!r}" for a in d.get("alerts", []) if a.get("level") not in ("alert", "escalation")]
        fr = d.get("fraud") or {}
        if fr.get("flagged") and not fr.get("reason"):
            bad.append(f"{n}: flagged without a reason")
        bad += [f"{n}: linked to unknown {x!r}" for x in fr.get("linked_deals", []) if x not in known and not x.startswith("H-")]
        reached = {e["stage"] for e in log}
        if fr.get("held") and "Closed" in reached and not any(r.get("type") == "fraud_release" and r.get("answer") == "release" for r in d.get("human_requests", [])):
            bad.append(f"{n}: money went out while held and never released")
        if fr.get("flagged") and "Closed" in reached and not fr.get("held"):
            bad.append(f"{n}: reached Closed with an uncleared fraud flag") if not any(r.get("answer") == "release" for r in d.get("human_requests", [])) else None
    if key:
        for n, want in key.get("deals", {}).items():
            d = deals.get(n)
            if d is None:
                continue
            fr = d.get("fraud") or {}
            if "fraud" in want and bool(fr.get("flagged")) != want["fraud"]:
                bad.append(f"{n}: fraud flagged={fr.get('flagged')}, expected {want['fraud']}")
            if "reached" in want:
                log = d.get("stage_log", [])
                last = log[-1] if log else {}
                got = "declined" if last.get("decision") == "declined" else (last.get("stage") if log else None)
                if got != want["reached"]:
                    bad.append(f"{n}: ended as {got}, expected {want['reached']}")
            bad += [f"{n}: missing issue {i}" for i in want.get("issues_include", []) if i not in d.get("issues", [])]
        missing = set(key.get("history_flags", [])) - set(sub.get("history_flags", []))
        bad += [f"history flag not found: {h}" for h in sorted(missing)]
        extra = set(sub.get("history_flags", [])) - set(key.get("history_flags", []))
        if "history_flags" in key:
            bad += [f"extra history flag (not in key): {h}" for h in sorted(extra)]
    return bad
