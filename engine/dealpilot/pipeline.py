"""The deal engine: a sim clock, files that "land", stage rules, human gates, stuck-deal alerts, fraud holds.

Everything the UI shows and everything submission.json contains comes from `Engine.state`.
Time moves in 15-minute internal steps so event timestamps stay close to when things really happen.
"""
import datetime as dt
import importlib.util
import json
import re
import uuid
from pathlib import Path

from .checks import check_deal, ref_date_for
from .extract import read_file, classify, PARSERS, parse_statement
from . import llm
from .detector import Context, rule_id, screen
from .fraud import History
from .patterns import Patterns
from .store import make_store
from .models import Forecast, StyleModel

STAGES = ["Intake", "Credit", "Committee", "Product", "Committed", "Closed"]
STEP = dt.timedelta(minutes=15)
INTAKE_DOCS = ["application", "guarantor_id", "certificate_of_formation", "ein_letter"]
INTAKE_MISMATCH = ("MISMATCH:legal_name", "MISMATCH:ein", "MISMATCH:amount_requested")
PRODUCT_FIT = {"Equipment Loan": {"equipment"}, "Revolving Line of Credit": {"inventory", "working_capital", "payroll"},
               "Working Capital Loan": {"working_capital", "payroll", "inventory"},
               "Term Loan": {"expansion", "refinance", "equipment", "working_capital"}}
ANSWERS = {  # request type -> allowed answers (first = default "yes")
    "credit_decision": ["approve", "decline", "request_info"],
    "committee_approval": ["approve", "decline"],
    "commitment_signoff": ["approve", "decline"],
    "funding_release": ["approve", "decline"],
    "fraud_hold": ["hold", "release"],
    "info_request": ["provided"],
    "fraud_release": ["confirm_fraud", "release"],   # first = what auto-answer does
}


def load_clock_module(data_dir):
    spec = importlib.util.spec_from_file_location("simclock", Path(data_dir) / "sim" / "clock.py")
    m = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(m)
    return m


def iso(t):
    return t.isoformat(timespec="minutes")


def hours(a, b):
    return (b - a).total_seconds() / 3600


class Engine:
    def __init__(self, data_dir, state_path, arrivals=None, store=None):
        self.data_dir = Path(data_dir)
        self.state_path = Path(state_path)
        self.clockmod = load_clock_module(self.data_dir)
        self.arrivals_path = Path(arrivals) if arrivals else self.data_dir / "sim" / "arrivals.jsonl"
        self.folder = self.data_dir / "shared_folder"
        self.rules = json.load(open(self.data_dir / "rules" / "stages.json"))
        teams = json.load(open(self.data_dir / "contacts" / "teams.json"))
        self.people = {p["id"]: p for p in teams["people"]}
        self.assign = json.load(open(self.data_dir / "sim" / "assignments.json"))
        self.store = store or make_store(self.state_path.parent / "store")
        self.state = json.load(open(self.state_path)) if self.state_path.exists() else None
        if self.state is None:
            self.reset()
        self.labels = self.store.list("labels")
        self.learned = {r["id"]: r for r in self.store.list("rules")}
        prior = [r for r in self.store.list("deals") if r.get("source") == "pipeline" and r.get("run_id") != self.state["run_id"]]
        self.hist = History(self.data_dir, extra=prior)   # earlier runs of the pipeline count as history
        self.style = StyleModel(self.data_dir)
        self.patterns = Patterns(self.hist)
        self.forecast = Forecast(self.data_dir)
        self.arrivals = {}
        for line in open(self.arrivals_path):
            if line.strip():
                r = json.loads(line)
                self.arrivals[r["path"]] = r["arrive_at"]
        self._ooo = self._parse_ooo()
        self._doc_cache = {}

    # ---------- state ----------
    def reset(self, start="2026-11-02T08:00", clear_store=False):
        self.state = {"now": iso(self.clockmod.parse_t(start)), "deals": {}, "intake_rr": 0, "req_seq": 0, "run_id": uuid.uuid4().hex[:8]}
        if clear_store and hasattr(self.store, "clear"):  # dev only: the local file store can start over; PocketBase never is wiped
            self.store.clear()
            self.labels, self.learned = [], {}
        self.save()

    def save(self):
        self.state_path.parent.mkdir(parents=True, exist_ok=True)
        json.dump(self.state, open(self.state_path, "w"), indent=1, default=list)

    def now(self):
        return self.clockmod.parse_t(self.state["now"])

    def _parse_ooo(self):
        out = {}
        for pid, p in self.people.items():
            m = re.search(r"OUT OF OFFICE (\d{4}-\d\d-\d\d \d\d:\d\d) through (\d{4}-\d\d-\d\d \d\d:\d\d)", p.get("notes", ""))
            if m:
                out[pid] = (self.clockmod.parse_t(m.group(1).replace(" ", "T")), self.clockmod.parse_t(m.group(2).replace(" ", "T")))
        return out

    def effective(self, pid, t):
        """Who actually handles things for `pid` at time t (backup if out of office)."""
        seen = set()
        while pid in self._ooo and self._ooo[pid][0] <= t <= self._ooo[pid][1] and pid not in seen:
            seen.add(pid)
            pid = self.people[pid]["backup"] or pid
        return pid

    def name(self, pid):
        return self.people[pid]["name"] if pid in self.people else pid

    # ---------- clock ----------
    def advance(self, delta):
        target = self.now() + (self.clockmod.parse_delta(delta) if isinstance(delta, str) else delta)
        return self.advance_to(target)

    def advance_to(self, target):
        t = self.now()
        while t < target:
            t = min(t + STEP, target)
            self.state["now"] = iso(t)
            self.process(t)
        self.save()
        return t

    # ---------- documents ----------
    def _doc(self, folder, fname):
        key = (folder, fname)
        if key not in self._doc_cache:
            p = self.folder / folder / fname
            info = read_file(p)
            kind = classify(info["text"], p)
            if kind == "bank_statement":
                data = parse_statement(info["text"], info["pages"])
            elif kind in PARSERS:
                data = PARSERS[kind](info["text"])
            else:
                data = {}
            self._doc_cache[key] = {"file": fname, "kind": kind, "producer": info["producer"], "created": info["created"],
                                    "pages": info["pages"], "data": data, "text": info["text"]}
        return self._doc_cache[key]

    def _arrived_files(self, t):
        by = {}
        for path, at in self.arrivals.items():
            if self.clockmod.parse_t(at) <= t and (self.folder / path).exists():
                folder, _, fname = path.partition("/")
                by.setdefault(folder, {})[fname] = at
        return by

    # ---------- main loop ----------
    def process(self, t):
        arrived = self._arrived_files(t)
        changed = False
        for folder, files in arrived.items():
            d = self.state["deals"].get(folder)
            if d is None:
                d = self._new_deal(folder, files, t)
            if sorted(files) != d["files"]:
                d["files"] = sorted(files)
                d["file_times"] = files
                self._refresh(d, t)
                changed = True
        if changed:
            self._fraud_screen(t)
        for d in self.state["deals"].values():
            self._step_deal(d, t)
            self._stuck_alerts(d, t)

    def _new_deal(self, folder, files, t):
        a = self.assign.get(folder, {})
        owners = ["olivia.chen", "jordan.webb"]
        intake = owners[self.state["intake_rr"] % 2]
        self.state["intake_rr"] += 1
        first = min(self.clockmod.parse_t(x) for x in files.values())
        d = {"name": folder, "rm": a.get("relationship_manager"), "credit_officer": a.get("credit_officer"),
             "intake_owner": intake, "files": [], "file_times": {}, "stage": "Intake", "stage_entered": iso(first),
             "status": "active", "facts": {}, "issues": [], "notes": {}, "stage_log": [], "human_requests": [],
             "alerts": [], "fraud": {"flagged": False, "held": False}, "requested": [], "waived": [],
             "alert_levels": {}, "waiting_on": "documents to arrive", "first_seen": iso(first), "extra_docs": []}
        self.state["deals"][folder] = d
        d["stage_log"].append({"stage": "Intake", "entered_at": iso(first), "exited_at": None, "decision": None, "why": None})
        return d

    def _refresh(self, d, t):
        docs = [self._doc(d["name"], f) for f in d["files"]]
        first = self.clockmod.parse_t(d["first_seen"]).date()
        f0 = check_deal(docs, first, self.hist)[0]
        ref = ref_date_for(f0, first)
        facts, issues, notes = check_deal(docs, ref, self.hist)
        need = [k for k in ("legal_name", "ein", "amount_requested", "product", "term_months", "guarantor_name") if not facts.get(k)]
        if need and docs:  # rules missed something -> ask local LLM for just those fields (no-op when no local model is running)
            for k, v in llm.fill_fields(need, "\n\n".join(x["text"] for x in docs if x["kind"] in ("application", "other", "request_letter"))).items():
                if k in need and v:
                    facts[k] = v
        facts["style"] = self.style.score(facts.get("dep_months"))
        d["facts"], d["notes"] = facts, notes
        d["issues"] = [i for i in issues if i not in d["waived"]]
        d["kinds"] = sorted({x["kind"] for x in docs})
        d["extra_docs"] = [x["file"] for x in docs if x["kind"] == "other"]
        if d["extra_docs"]:
            for fname in d["extra_docs"]:
                key = f"EXTRA:{fname}"
                if key not in d["requested"]:
                    d["requested"].append(key)
                    self._alert(d, t, "alert", d["rm"], d["stage"],
                                f"{d['name']} (RM {self.name(d['rm'])}): a new document '{fname}' arrived that I could not classify "
                                f"(possibly a bank/collections notice). A person should read it. Unblocks: RM reviews and tells me what it means.")

    # ---------- fraud ----------
    def _fraud_screen(self, t):
        deals = self.state["deals"]
        cur = {n: d["facts"] for n, d in deals.items() if d["facts"] and d["facts"].get("legal_name")}
        ctx = Context(self.hist, self.patterns, cur, self.labels, self.learned)
        for n, d in deals.items():
            if n not in cur or d["status"] in ("declined", "withdrawn") or d["fraud"].get("cleared"):
                continue
            res = screen({"name": n, "facts": d["facts"], "issues": d["issues"], "notes": d["notes"]}, ctx)
            self._propose_rules(n, res, t)
            if res["level"] == "none":
                continue
            fr = d["fraud"]
            fr.update({"linked_deals": res["linked_deals"], "reason": res["reason"], "score": res["score"],
                       "level": res["level"], "rules": [f["rule"] for f in res["findings"] if f["level"] != "info"]})
            if res["level"] == "critical" and not fr.get("queued"):
                fr.update({"queued": True, "flagged": True, "held": False, "flagged_at": iso(t), "stage_at_flag": d["stage"]})
                self.store.upsert("fraud_critical", n, {
                    "id": n, "folder": n, "label": "fraud critical", "status": "open",
                    "at": iso(t), "stage": d["stage"], "reason": res["reason"],
                })
            elif res["level"] == "warning" and not fr.get("warned"):
                fr.update({"warned": True, "warned_at": iso(t), "held": False})
                self.store.upsert("fraud_warnings", n, {
                    "id": n, "folder": n, "status": "open", "at": iso(t), "stage": d["stage"], "reason": res["reason"],
                })
                self._alert(d, t, "alert", "victor.hale", d["stage"], self._fraud_message(d, "warning"))

    def _propose_rules(self, name, res, t):
        """A new cluster of shared rare details becomes a proposed rule for Fraud Ops to approve or reject."""
        for f in res["findings"]:
            sig = f.get("signature")
            if sig and len(sig) >= 3 and rule_id(sig) not in self.learned:
                rec = {"id": rule_id(sig), "status": "proposed", "tokens": sig, "evidence": f["evidence"], "created_at": iso(t),
                       "support": sorted({name} | {x for x in f["deals"] if not x.startswith("P-") and not x.startswith("H-")}),
                       "run_id": self.state["run_id"]}
                self.learned[rec["id"]] = rec
                self.store.upsert("rules", rec["id"], rec)

    def decide_rule(self, rid, decision):
        """Fraud Ops approves or rejects a proposed pattern; approved patterns apply to every deal from now on."""
        rec = self.learned[rid]
        rec["status"] = {"approve": "active", "reject": "rejected"}[decision]
        rec["decided_at"] = self.state["now"]
        self.store.upsert("rules", rid, rec)
        self._fraud_screen(self.now())
        self.save()

    def decide_fraud(self, folder, decision):
        """Fraud team answer for a critical queue row. The deal does not stop before Committed."""
        if decision not in {"release", "reject"}:
            raise ValueError(decision)
        deal = self.state["deals"][folder]
        deal["fraud"]["decision"] = decision
        if decision == "release":
            deal["fraud"]["cleared"] = True
            deal["fraud"]["held"] = False
        rows = [row for row in self.store.list("fraud_critical") if row.get("folder") == folder]
        rec = dict(rows[0]) if rows else {"id": folder, "folder": folder, "label": "fraud critical"}
        rec["status"] = "released" if decision == "release" else "rejected"
        self.store.upsert("fraud_critical", folder, rec)
        self.save()

    def _fraud_message(self, d, level):
        f, fr = d["facts"], d["fraud"]
        who = f"{d['name']} ({f.get('legal_name')}, ${f.get('amount_requested') or 0:,.0f}, RM {self.name(d['rm'])})"
        linked = fr.get("linked_deals", [])
        rel = (f" Related deals: current {[x for x in linked if not x.startswith('H-')] or 'none'}; "
               f"history {[x for x in linked if x.startswith('H-')] or 'none'} (funded ones may need a look).") if linked else ""
        if level == "warning":
            return (f"WARNING (not held): a new pattern on {who} needs a look when you have time. The deal keeps moving. "
                    f"Why: {fr['reason']}{rel}")
        return (f"Fraud critical: {who} stays on the fraud queue. The deal keeps moving until Committed, then waits for the fraud team. "
                f"Why: {fr['reason']}{rel}")

    # ---------- humans ----------
    def _request(self, d, t, rtype, to, message, stage=None, issues=None):
        self.state["req_seq"] += 1
        req = {"id": f"R{self.state['req_seq']}", "type": rtype, "to": to, "at": iso(t), "message": message,
               "answer": None, "answered_at": None, "stage": stage or d["stage"], "consumed": False,
               "options": ANSWERS.get(rtype, []), "issues": issues or []}
        if d["facts"].get("needed_by"):
            req["urgent_by"] = d["facts"]["needed_by"]
            req["message"] = f"URGENT (funds needed by {d['facts']['needed_by']}): " + req["message"]
        d["human_requests"].append(req)
        return req

    def answer(self, deal, req_id, answer, by=None):
        t = self.now()
        d = self.state["deals"][deal]
        req = next(r for r in d["human_requests"] if r["id"] == req_id)
        if req["answer"] is not None:
            return req
        req["answer"], req["answered_at"], req["answered_by"] = answer, iso(t), by
        if req["type"] == "fraud_hold":
            if answer == "release":
                self._clear(d, t)
            else:  # held is a MIDDLE state: Fraud Ops now has to release it or confirm the fraud
                d["fraud"].update({"held": True, "held_at": iso(t)})
                d["status"] = "held"
                d["waiting_on"] = "Fraud Operations (release or confirm fraud)"
                self._request(d, t, "fraud_release", "victor.hale",
                              f"{d['name']} is on fraud hold at {d['stage']}. Release it (it carries on toward Closed) or confirm the fraud "
                              f"(it is declined and kept as a known fraud). Why it was held: {d['fraud'].get('reason', '')}", stage=d["stage"])
        elif req["type"] == "fraud_release":
            if answer == "release":
                self._clear(d, t)
            else:
                d["fraud"]["confirmed"] = True
                d["status"] = "declined"
                self._label(d, t, "confirmed")
                self._close_stage(d, t, "declined", "Fraud confirmed by Fraud Operations; deal declined.")
        elif req["type"] == "info_request":
            for i in req["issues"]:
                if i not in d["waived"]:
                    d["waived"].append(i)
            d["issues"] = [i for i in d["issues"] if i not in d["waived"]]
        self.process(t)
        self.save()
        return req

    def _clear(self, d, t):
        """Fraud Ops decided it is fine: deal resumes where it paused; this pattern is recorded as a false alarm."""
        d["fraud"].update({"held": False, "cleared": True, "released_at": iso(t)})
        if d["status"] == "held":
            d["status"] = "active"
            d["waiting_on"] = None
        for r in d["human_requests"]:
            if r["type"] in ("fraud_hold", "fraud_release") and r["answer"] is None:
                r["answer"], r["answered_at"], r["answered_by"] = "superseded", iso(t), "system"
        self._label(d, t, "cleared")

    def _label(self, d, t, label):
        """Feedback the detector learns from: confirmed fraud or cleared false alarm."""
        fr = d["fraud"]
        rec = {"id": f"{self.state['run_id']}:{d['name']}", "deal": d["name"], "label": label, "status": label, "rules": fr.get("rules", []),
               "linked": fr.get("linked_deals", []), "run_id": self.state["run_id"], "at": iso(t)}
        fr["label"] = label
        self.labels.append(rec)
        self.store.upsert("labels", rec["id"], rec)

    def _finalize(self, d, t, status):
        """A deal that reached Closed / Declined / Withdrawn becomes history for later runs."""
        if d.get("finalized"):
            return
        d["finalized"] = True
        label = d["fraud"].get("label", "none")
        rec = {"id": f"P-{d['name']}@{self.state['run_id'][:4]}", "source": "pipeline", "run_id": self.state["run_id"], "status": status,
               "label": label, "bad": label == "confirmed", "facts": d["facts"], "at": iso(t)}
        self.store.upsert("deals", f"{self.state['run_id']}:{d['name']}", rec)

    def pending(self):
        out = []
        for d in self.state["deals"].values():
            for r in d["human_requests"]:
                if r["answer"] is None:
                    out.append((d["name"], r))
        return out

    def auto_answer(self, skip_people=(), only_older_than_h=0):
        """Play the humans: yes to sign-offs, hold for fraud, 'provided' for borrower info. Returns count."""
        n = 0
        for name, r in self.pending():
            if r["to"] in skip_people:
                continue
            if hours(self.clockmod.parse_t(r["at"]), self.now()) < only_older_than_h:
                continue
            self.answer(name, r["id"], r["options"][0], by=f"auto:{r['to']}")
            n += 1
        return n

    # ---------- stage machine ----------
    def _close_stage(self, d, t, decision, why):
        cur = d["stage_log"][-1]
        if cur["exited_at"] is None:
            cur.update({"exited_at": iso(t), "decision": decision, "why": why})
        if decision in ("declined", "withdrawn", "funded"):
            self._finalize(d, t, {"funded": "closed"}.get(decision, decision))

    def _enter(self, d, t, stage, why):
        self._close_stage(d, t, "advanced", why)
        d["stage"] = stage
        d["stage_entered"] = iso(t)
        d["stage_log"].append({"stage": stage, "entered_at": iso(t), "exited_at": None, "decision": None, "why": None})
        d["waiting_on"] = None
        for r in d["human_requests"]:
            if r["stage"] != stage:
                r["consumed"] = True

    def _gate(self, d, t, rtype, to, message):
        """-> answer string once a human has answered, else None (request created/waiting)."""
        reqs = [r for r in d["human_requests"] if r["type"] == rtype and r["stage"] == d["stage"] and not r["consumed"]]
        if not reqs:
            self._request(d, t, rtype, to, message)
            d["waiting_on"] = f"{self.name(to)} ({rtype.replace('_', ' ')})"
            return None
        r = reqs[-1]
        if r["answer"] is None:
            d["waiting_on"] = f"{self.name(r['to'])} ({rtype.replace('_', ' ')})"
            return None
        r["consumed"] = True
        return r["answer"]

    def _blocking(self, d, pred):
        return [i for i in d["issues"] if pred(i)]

    def _ask_borrower(self, d, t, blockers):
        new = [i for i in d["issues"] if i not in d["requested"] and not i.startswith("OLD_TRICK")]
        if new:
            items = "; ".join(d["notes"].get(i, i) for i in new)
            self._request(d, t, "info_request", "borrower",
                          f"Hello {d['facts'].get('guarantor_name') or ''}, to keep your loan moving we still need: {items}",
                          issues=new)
            d["requested"].extend(new)
        d["waiting_on"] = "borrower: " + ", ".join(blockers)

    def _step_deal(self, d, t):
        if d["status"] != "active":
            return
        fr = d["fraud"]
        if fr.get("flagged") and fr.get("held"):
            reqs = [r for r in d["human_requests"] if r["type"] == "fraud_hold" and r["answer"] is None]
            d["waiting_on"] = "Fraud Operations (fraud hold decision)" if reqs else "fraud hold in place"
            return
        getattr(self, "_do_" + d["stage"].lower())(d, t)

    def _do_intake(self, d, t):
        missing = [k for k in INTAKE_DOCS if k not in d.get("kinds", [])]
        if missing:
            d["waiting_on"] = "documents still arriving: " + ", ".join(missing)
            blockers = [i for i in d["issues"] if i.startswith("MISSING_DOC:") and i.split(":")[1] in INTAKE_DOCS]
            if blockers and hours(self.clockmod.parse_t(d["stage_entered"]), t) >= 1:
                self._ask_borrower(d, t, blockers)
            return
        if any(self.clockmod.parse_t(d["file_times"][f]) + dt.timedelta(minutes=5) > t for f in d["files"]):
            return  # still receiving files
        blockers = self._blocking(d, lambda i: i in INTAKE_MISMATCH or (i.startswith("MISSING_DOC:") and i.split(":")[1] in INTAKE_DOCS))
        if blockers:
            self._ask_borrower(d, t, blockers)
            return
        f = d["facts"]
        self._enter(d, t, "Credit", f"All intake documents present and facts consistent: {f['legal_name']}, EIN {f['ein']}, "
                                    f"${f['amount_requested']:,.0f} {f['product']} for {f['purpose_category']}, guarantor {f['guarantor_name']}.")

    def _credit_blockers(self, d):
        return self._blocking(d, lambda i: i.startswith("MISSING_PAGE:") or (i.startswith("MISSING_DOC:") and (
            i.startswith("MISSING_DOC:bank_statement") or i.endswith(("financial_statements", "tax_return")))))

    def _do_credit(self, d, t):
        blockers = self._credit_blockers(d)
        if blockers:
            self._ask_borrower(d, t, blockers)
            return
        officer = self.effective(d["credit_officer"], t)
        f = d["facts"]
        flags = [d["notes"].get(i, i) for i in d["issues"]]
        msg = (f"Credit decision needed for {d['name']} ({f['legal_name']}): ${f['amount_requested']:,.0f} {f['product']} for {f['purpose_category']}, "
               f"{f['term_months']} months, stated revenue ${f['annual_revenue_stated'] or 0:,.0f}, guarantor {f['guarantor_name']}. "
               f"Documents complete (6 statements, financials, tax return/extension). "
               + ("Things to know: " + " | ".join(flags) if flags else "No discrepancies found.") + " Approve, decline, or request more info?")
        ans = self._gate(d, t, "credit_decision", officer, msg)
        if ans == "approve":
            self._enter(d, t, "Committee", f"Credit approved by {self.name(officer)}.")
        elif ans == "decline":
            d["status"] = "declined"
            self._close_stage(d, t, "declined", f"Credit declined by {self.name(officer)}.")
        elif ans == "request_info":
            self._request(d, t, "info_request", "borrower", "The credit officer needs more information about your business to finish review.")

    def _do_committee(self, d, t):
        f = d["facts"]
        r = self.rules["stages"]["Committee"]["routing"]
        big = f["amount_requested"] > r["delegated_max_amount"]
        approver = r["full_committee_chair"] if big else r["delegated_approver"]
        memo = self._memo(d)
        d["memo"] = memo
        ans = self._gate(d, t, "committee_approval", self.effective(approver, t),
                         f"{'Full committee chair' if big else 'Delegated approval'} needed for {d['name']} (${f['amount_requested']:,.0f}). Credit memo: {memo}")
        if ans == "approve":
            self._enter(d, t, "Product", f"Approved by {self.name(approver)}.")
        elif ans == "decline":
            d["status"] = "declined"
            self._close_stage(d, t, "declined", f"Committee declined ({self.name(approver)}).")

    def _memo(self, d):
        f = d["facts"]
        done = [f"{s['stage']}: {s['why']}" for s in d["stage_log"] if s["decision"] == "advanced"]
        return (f"{f['legal_name']} requests ${f['amount_requested']:,.0f} ({f['product']}, {f['term_months']} mo) for {f['purpose_category']}. "
                + " ".join(done) + (" Open flags: " + "; ".join(d["notes"].values()) if d["notes"] else " No open flags."))

    def _do_product(self, d, t):
        f = d["facts"]
        if f["product"] == "Equipment Loan":
            if "equipment_quote" not in d.get("kinds", []):
                self._ask_borrower(d, t, ["MISSING_DOC:equipment_quote"])
                return
            vend = f"vendor '{f['vendor']}' remit account …{f['remit_acct_last4']} checked against known vendors"
        else:
            vend = "no vendor payment"
        fits = f["purpose_category"] in PRODUCT_FIT.get(f["product"], set())
        why = (f"{f['product']} at {f['term_months']} months is consistent with the request ({f['purpose_category']})" if fits else
               f"{f['product']} chosen as requested; note purpose '{f['purpose_category']}' is unusual for it") + f"; {vend}."
        self._enter(d, t, "Committed", why)

    def _do_committed(self, d, t):
        rm = self.effective(d["rm"], t)
        f = d["facts"]
        ans = None if self._signed(d) else self._gate(
            d, t, "commitment_signoff", rm,
            f"Please sign the commitment letter for {d['name']} (${f['amount_requested']:,.0f} {f['product']}, {f['term_months']} months) with the borrower.")
        if ans == "decline":
            d["status"] = "withdrawn"
            self._close_stage(d, t, "withdrawn", "RM did not sign the commitment.")
            return
        if ans != "approve" and not self._signed(d):
            return
        if d["fraud"].get("level") == "critical" and d["fraud"].get("decision") != "release":
            if d["fraud"].get("decision") == "reject":
                d["status"] = "declined"
                d["waiting_on"] = "fraud critical"
                self._close_stage(d, t, "declined", "Fraud team rejected the deal at Committed.")
                return
            d["waiting_on"] = "fraud critical"
            return
        if f["product"] != "Equipment Loan" and f["app_acct_last4"] not in f["stmt_acct_last4"]:
            d["waiting_on"] = "disbursement account does not match the statements"
            return
        ans2 = self._gate(d, t, "funding_release", "tasha.reed",
                          f"Release ${f['amount_requested']:,.0f} for {d['name']}? Final fraud screen is clear and the disbursement account "
                          + (f"is the verified vendor account …{f['remit_acct_last4']}." if f["product"] == "Equipment Loan" else f"is the borrower's own account …{f['app_acct_last4']}."))
        if ans2 == "approve":
            self._enter(d, t, "Closed", "Commitment signed; final fraud screen clear; funding released by Tasha Reed.")
            self._close_stage(d, t, "funded", "Funds released.")
            d["status"] = "closed"
            d["waiting_on"] = None
        elif ans2 == "decline":
            d["status"] = "withdrawn"
            self._close_stage(d, t, "withdrawn", "Funding officer declined release.")

    def _signed(self, d):
        return any(r["type"] == "commitment_signoff" and r["answer"] == "approve" for r in d["human_requests"] if r["stage"] == "Committed")

    # ---------- stuck deals ----------
    def _sla(self, d):
        s = self.rules["stages"][d["stage"]]
        if d["stage"] == "Committee" and d["facts"].get("amount_requested", 0) <= s["routing"]["delegated_max_amount"]:
            return s["sla_hours_delegated"]
        return s["sla_hours"]

    def _owner(self, d, t):
        pend = [r for r in d["human_requests"] if r["answer"] is None and r["to"] != "borrower"]
        if pend:
            return pend[-1]["to"]
        return {"Intake": d["intake_owner"], "Credit": self.effective(d["credit_officer"], t), "Committee": "priscilla.nguyen",
                "Product": "kevin.alvarez", "Committed": self.effective(d["rm"], t), "Closed": "tasha.reed"}[d["stage"]]

    def _alert(self, d, t, level, to, stage, message):
        d["alerts"].append({"at": iso(t), "stage": stage, "level": level, "to": to, "message": message})

    def _stuck_alerts(self, d, t):
        if d["status"] not in ("active",) or d["stage"] == "Closed":
            return
        el = hours(self.clockmod.parse_t(d["stage_entered"]), t)
        sla = self._sla(d)
        pct = el / sla * 100
        sent = d["alert_levels"].setdefault(d["stage"], 0)
        sp = self.rules["stuck_deals"]
        levels = [sp["alert_at_pct_of_sla"], sp["escalate_at_pct_of_sla"], sp["second_escalation_at_pct_of_sla"]]
        if sent >= 3 or pct < levels[sent]:
            return
        owner = self._owner(d, t)
        waiting = d.get("waiting_on") or "the next step"
        who = f"{d['name']} ({d['facts'].get('legal_name', '')}, RM {self.name(d['rm'])})"
        base = f"{who} has been in {d['stage']} for {el:.0f}h (limit {sla}h) waiting on {waiting}."
        unblock = self._unblock(d)
        if sent == 0:
            for to in dict.fromkeys([owner, d["rm"]]):
                self._alert(d, t, "alert", to, d["stage"], f"{base} To unblock: {unblock}")
        elif sent == 1:
            self._alert(d, t, "escalation", self.people[owner]["escalates_to"] or "lauren.kim", d["stage"],
                        f"ESCALATION: {base} Owner {self.name(owner)} has not cleared it ({pct:.0f}% of limit). To unblock: {unblock}")
        else:
            self._alert(d, t, "escalation", sp["second_escalation_to"], d["stage"],
                        f"SECOND ESCALATION: {base} Still stuck at {pct:.0f}% of limit. To unblock: {unblock}")
        d["alert_levels"][d["stage"]] = sent + 1

    def _unblock(self, d):
        pend = [r for r in d["human_requests"] if r["answer"] is None]
        if pend:
            r = pend[-1]
            return (f"{self.name(r['to']) if r['to'] != 'borrower' else 'the borrower'} must answer the {r['type'].replace('_', ' ')} request"
                    + (f" (funds needed by {d['facts']['needed_by']})" if d["facts"].get("needed_by") else "") + ".")
        return "the missing item has to arrive or a person has to review the deal."

    # ---------- forecasts ----------
    def risk(self, d):
        """When will this deal reach Closed (p50/p90), and does that beat 'funds needed by'? None if not live."""
        if d["status"] != "active" or d["stage"] == "Closed" or not d["facts"].get("legal_name"):
            return None
        t = self.now()
        pend = any(r["answer"] is None for r in d["human_requests"])
        blocked = pend or (d.get("waiting_on") or "").startswith("borrower")
        later = [(s, self.rules["stages"][s]["sla_hours"]) for s in STAGES[STAGES.index(d["stage"]) + 1:]]
        p50, p90 = self.forecast.project(d["stage"], self.clockmod.parse_t(d["stage_entered"]), self._sla(d), blocked, later, t)
        out = {"p50": iso(p50), "p90": iso(p90), "blocked": blocked}
        nb = d["facts"].get("needed_by")
        if nb:
            need = self.clockmod.parse_t(nb)
            out["needed_by"] = nb
            out["deadline"] = "at risk" if p90 > need else "on track"
            out["deadline_p50_ok"] = p50 <= need
        return out

    def baseline_note(self, d):
        """Returning borrower asking for much more than their own history -> plain-words note."""
        f = d["facts"]
        prior = [h["amount_requested"] for h in self.hist.facts.values() if h["ein"] == f.get("ein")]
        if prior and f.get("amount_requested") and f["amount_requested"] > 1.5 * max(prior):
            return f"Asking ${f['amount_requested']:,.0f}, well above this borrower's past requests (max ${max(prior):,.0f})."
        return None

    # ---------- reporting ----------
    def _export_stages(self, d):
        """A deal on hold is mid-stage in our state; the submission says where and why it stopped."""
        log = [dict(x) for x in d["stage_log"]]
        if d["status"] == "held" and log and log[-1]["exited_at"] is None:
            log[-1].update({"exited_at": d["fraud"].get("held_at"), "decision": "held",
                            "why": "Fraud hold placed by Fraud Operations; waiting for them to release it or confirm the fraud."})
        return log

    def export(self, team="DealPilot", set_name="sample"):
        deals = {}
        hist_flags = set()
        for n, d in self.state["deals"].items():
            f = d["facts"]
            fr = d["fraud"]
            hist_flags |= {x for x in fr.get("linked_deals", []) if x.startswith("H-")}
            deals[n] = {
                "extracted": {k: f.get(k) for k in ("legal_name", "ein", "amount_requested", "purpose_category", "product",
                                                    "term_months", "guarantor_name", "annual_revenue_stated", "needed_by")},
                "issues": list(dict.fromkeys(d["issues"] + d["waived"])),
                "stage_log": self._export_stages(d),
                "human_requests": [{k: r.get(k) for k in ("type", "to", "at", "message", "answer", "answered_at")} for r in d["human_requests"]],
                "alerts": d["alerts"],
                "fraud": {"flagged": fr.get("flagged", False), "flagged_at": fr.get("flagged_at"), "stage_at_flag": fr.get("stage_at_flag"),
                          "held": bool(fr.get("held")), "reason": fr.get("reason"), "linked_deals": fr.get("linked_deals", []),
                          "level": fr.get("level"), "confirmed": bool(fr.get("confirmed")), "cleared": bool(fr.get("cleared"))},
                "status": d["status"],
            }
        return {"team": team, "set": set_name, "deals": deals, "history_flags": sorted(hist_flags)}
