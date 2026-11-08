"""Cross-deal fraud linking.

Idea: one deal can look fine; the trick shows when unrelated borrowers share RARE things.
Every deal (current + history) is turned into attributes. An attribute value shared by few deals is
strong evidence (weight = ln(N/count)); one shared by many (a popular registered agent) is almost nothing.
Two deals of DIFFERENT borrowers are linked when their shared weight is high and comes from several
different kinds of attribute. Connected links form a ring.
"""
import csv
import datetime as dt
import math
import re
from collections import Counter, defaultdict
from pathlib import Path

from .checks import building, norm_addr, norm_name

LINK_SUM = 10.0      # total shared weight needed to link two deals
LINK_TYPES = 2       # ...from at least this many different attribute kinds
TYPE_MIN_W = 3.0     # a kind only counts as "different evidence" if its own weight is at least this
IDENTITY_KINDS = {"guarantor_dl", "guarantor_ssn4_dob"}  # same person behind "different" businesses: link on their own
THRESHOLDS = (250_000,)  # approval thresholds a fraudster may sit just under


def months_between(a, b):
    return (b.year - a.year) * 12 + b.month - a.month


def attrs(f, history_row=False):
    """Facts -> list of (kind, value). Only kinds observable in this deal are emitted."""
    out = []

    def add(kind, v):
        if v not in (None, "", []):
            out.append((kind, v))

    if f.get("remit_acct_last4"):
        add("remit_acct", f"{f.get('remit_routing') or ''}/{f['remit_acct_last4']}".lstrip("/"))
    add("registered_agent", norm_name(f.get("registered_agent")) or None)
    if f.get("preparer_name") and f["preparer_name"] != "Self-prepared":
        add("preparer", norm_name(f["preparer_name"] + " " + (f.get("preparer_firm") or "")))
    add("pdf_producer", f.get("pdf_producer"))
    if not history_row:
        for p in f.get("statement_producers") or []:
            add("statement_producer", p)
    add("business_building", building(f.get("business_address")))
    add("guarantor_home", norm_addr(f.get("guarantor_address")))
    add("guarantor_dl", re.sub(r"\D", "", f.get("guarantor_dl") or "") or None)
    add("guarantor_ssn4_dob", f"{f.get('guarantor_ssn4')}/{f.get('guarantor_dob')}" if f.get("guarantor_ssn4") else None)
    add("business_phone", f.get("phone"))
    add("bank_name", f.get("bank_name"))
    amt = f.get("amount_requested")
    for t in THRESHOLDS:
        if amt and 0.95 * t <= amt < t:
            add("just_under_threshold", f"<{t}")
    try:
        formed = dt.date.fromisoformat(f["formation_date"])
        ref = dt.date.fromisoformat(f.get("ref_date") or f["signed_date"])
        add("young_entity", "under_18_months") if months_between(formed, ref) < 18 else None
    except Exception:
        pass
    rs = f.get("round_deposit_share")
    if rs is not None and rs >= 0.8:
        add("round_number_deposits", "mostly_round")
    for a in f.get("outbound_transfer_accts") or []:
        add("outbound_acct", a)
    return out


class History:
    """History deals.csv (+ deposit style) as facts, with indexes for DUP_EIN and vendor checks."""

    def __init__(self, data_dir, extra=()):
        d = Path(data_dir) / "history"
        self.rows = list(csv.DictReader(open(d / "deals.csv")))
        dep = defaultdict(list)
        for r in csv.DictReader(open(d / "monthly_deposits.csv")):
            dep[r["deal_id"]].append(float(r["pct_deposits_multiple_of_100"] or 0))
        self.facts = {}
        for r in self.rows:
            self.facts[r["deal_id"]] = {
                "id": r["deal_id"], "legal_name": r["legal_name"], "ein": r["ein"], "business_address": r["business_address"],
                "registered_agent": r["registered_agent"], "preparer_name": r["preparer_name"],
                "preparer_firm": r["preparer_firm"], "preparer_phone": r["preparer_phone"], "bank_name": r["bank_name"],
                "pdf_producer": r["pdf_producer"], "vendor": r["equipment_vendor"],
                "remit_acct_last4": r["vendor_remit_acct_last4"], "amount_requested": float(r["amount_requested"] or 0),
                "formation_date": r["formation_date"], "ref_date": r["application_date"],
                "guarantor_dl": r["guarantor_dl_number"], "guarantor_ssn4": r["guarantor_ssn_last4"],
                "guarantor_dob": r["guarantor_dob"], "registered_agent_address": r["registered_agent_address"],
                "product": r["product"], "term_months": int(r["term_months"] or 0), "purpose_category": r["purpose"],
                "bad": r["repayment_status"] in ("30_days_past_due", "charged_off") or r["final_status"] == "fraud_confirmed",
                "round_deposit_share": (sum(dep[r["deal_id"]]) / len(dep[r["deal_id"]])) if dep[r["deal_id"]] else None,
            }
        for rec in extra:  # deals the pipeline finished in earlier runs: same columns, plus our own outcome
            f = dict(rec["facts"], id=rec["id"], bad=rec.get("bad", False))
            self.facts[f["id"]] = f
        self._ein = defaultdict(set)
        for f in self.facts.values():
            self._ein[f["ein"]].add(norm_name(f["legal_name"]))

    def names_for_ein(self, ein):
        return self._ein.get(ein, set())


def explain(kind, value):
    return {
        "remit_acct": f"same vendor payment account {value}",
        "vendor_name": f"same vendor name '{value}'",
        "registered_agent": f"same registered agent '{value}'",
        "preparer": f"same accountant/preparer '{value}'",
        "preparer_phone": f"same preparer phone {value}",
        "pdf_producer": f"same PDF software on the application ('{value}')",
        "statement_producer": f"bank statements made with the same software ('{value}')",
        "business_building": f"same building '{value}'",
        "guarantor_home": f"same guarantor home address '{value}'",
        "guarantor_dl": f"same driver license {value}",
        "guarantor_ssn4_dob": f"same SSN4/DOB {value}",
        "business_phone": f"same business phone {value}",
        "bank_name": f"same operating bank '{value}'",
        "just_under_threshold": "amount sits just under the $250,000 committee line",
        "young_entity": "business formed less than 18 months ago",
        "round_number_deposits": "bank deposits are almost all round hundreds",
        "outbound_acct": f"money sweeps out to the same account XXXX{value} every month",
    }.get(kind, f"{kind}={value}")


class Linker:
    """Builds the rarity index over history + current deals and finds rings."""

    def __init__(self, hist):
        self.hist = hist

    def run(self, current, report_history=False):
        """current: {deal_name: facts}. -> {deal_name: {'linked': [...], 'evidence': [...], 'score': x}}"""
        nodes = {}
        for hid, f in self.hist.facts.items():
            nodes[hid] = (f, attrs(f, history_row=True))
        for name, f in current.items():
            nodes[name] = (f, attrs(f))
        cnt = Counter(kv for _, at in nodes.values() for kv in set(at))
        total = len(nodes)

        def weight(kind, v):
            return math.log(total / cnt[(kind, v)]) if cnt[(kind, v)] else 0.0

        by_val = defaultdict(list)
        for nid, (f, at) in nodes.items():
            for kv in set(at):
                by_val[kv].append(nid)
        pair = defaultdict(list)  # (a,b) -> [(kind, value, w)]
        for (kind, v), ids in by_val.items():
            if len(ids) < 2 or len(ids) > 40:
                continue
            w = weight(kind, v)
            for i in range(len(ids)):
                for j in range(i + 1, len(ids)):
                    pair[tuple(sorted((ids[i], ids[j])))].append((kind, v, w))
        links = defaultdict(list)
        for (a, b), shared in pair.items():
            fa, fb = nodes[a][0], nodes[b][0]
            if fa.get("ein") and fa["ein"] == fb.get("ein"):
                continue  # same borrower returning, not a ring
            if norm_name(fa.get("legal_name")) == norm_name(fb.get("legal_name")):
                continue
            strong = {k for k, v, w in shared if w >= TYPE_MIN_W}
            total_w = sum(w for _, _, w in shared)
            undisclosed = all((x.get("affiliated") or "none").strip().lower() in ("none", "—", "-", "n/a", "no") for x in (fa, fb))
            identity = undisclosed and any(k in IDENTITY_KINDS and w >= TYPE_MIN_W for k, v, w in shared)
            if (total_w >= LINK_SUM and len(strong) >= LINK_TYPES) or identity:
                links[a].append((b, total_w, shared, identity))
                links[b].append((a, total_w, shared, identity))
        # connected components
        seen, comps = set(), []
        for n in links:
            if n in seen:
                continue
            stack, comp = [n], set()
            while stack:
                x = stack.pop()
                if x in comp:
                    continue
                comp.add(x)
                stack.extend(y[0] for y in links[x])
            seen |= comp
            comps.append(comp)
        result = {}
        for comp in comps:
            cur = [n for n in comp if n in current or report_history]
            if not cur:
                continue
            for n in cur:
                direct = sorted(links[n], key=lambda x: -x[1])
                ev = []
                for other, w, shared, _ in direct[:2]:
                    ev.append(f"vs {other}: " + "; ".join(explain(k, v) for k, v, ww in sorted(shared, key=lambda x: -x[2])[:6]))
                result[n] = {"linked": sorted(comp - {n}), "evidence": ev, "score": round(max(x[1] for x in direct), 1),
                             "identity": any(x[3] for x in direct)}
        return result
