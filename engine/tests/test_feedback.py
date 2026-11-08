"""Held is a middle state; Fraud Ops feedback is learned; proposed patterns can be approved or rejected."""
import datetime as dt
import json
from collections import Counter
from pathlib import Path

import pytest

from dealpilot.detector import Context, adjust, finding, rule_id, screen
from dealpilot.pipeline import Engine
from dealpilot.store import FileStore

DATA = next(p for p in (Path(__file__).resolve().parent.parent / "data", Path(__file__).resolve().parent.parent.parent) if (p / "shared_folder").exists())


def engine(tmp_path, folders, store=None):
    arr = tmp_path / "arrivals.jsonl"
    rows = [json.loads(l) for l in open(DATA / "sim" / "arrivals.jsonl") if l.strip()]
    arr.write_text("\n".join(json.dumps(r) for r in rows if r["path"].split("/")[0] in folders))
    return Engine(DATA, tmp_path / "state.json", arr, store or FileStore(tmp_path / "store"))


def run_until_hold(e):
    e.advance("1d")
    return next(r for n, r in e.pending() if r["type"] == "fraud_hold")


def test_hold_is_a_middle_state_release_resumes_to_closed(tmp_path):
    e = engine(tmp_path, {"Ridgeway Freight Logistics"})
    req = run_until_hold(e)
    e.answer("Ridgeway Freight Logistics", req["id"], "hold")
    d = e.state["deals"]["Ridgeway Freight Logistics"]
    assert d["status"] == "held" and d["stage_log"][-1]["exited_at"] is None and not d.get("finalized")
    rel = next(r for r in d["human_requests"] if r["type"] == "fraud_release" and r["answer"] is None)
    assert e._export_stages(d)[-1]["decision"] == "held"           # the submission says where it stopped
    e.answer(d["name"], rel["id"], "release")
    assert d["status"] == "active" and d["fraud"]["cleared"] and d["fraud"]["label"] == "cleared"
    for _ in range(12):                                             # carries on through the normal gates
        e.advance("12h")
        e.auto_answer()
    assert d["status"] == "closed" and d["stage"] == "Closed"
    assert any(l["label"] == "cleared" for l in e.store.list("labels"))


def test_confirm_declines_and_next_run_sees_it_as_history(tmp_path):
    e = engine(tmp_path, {"Ridgeway Freight Logistics"})
    req = run_until_hold(e)
    e.answer("Ridgeway Freight Logistics", req["id"], "hold")
    d = e.state["deals"]["Ridgeway Freight Logistics"]
    rel = next(r for r in d["human_requests"] if r["type"] == "fraud_release" and r["answer"] is None)
    e.answer(d["name"], rel["id"], "confirm_fraud")
    assert d["status"] == "declined" and d["stage_log"][-1]["decision"] == "declined" and d["fraud"]["confirmed"]
    saved = [r for r in e.store.list("deals") if r["source"] == "pipeline"]
    assert saved and saved[0]["bad"] and saved[0]["label"] == "confirmed"
    e2 = Engine(DATA, tmp_path / "state2.json", e.arrivals_path, e.store)   # a later run of the same system
    prior = [f for f in e2.hist.facts.values() if str(f["id"]).startswith("P-Ridgeway")]
    assert prior and prior[0]["bad"]


class Ctx:
    def __init__(self, confirmed=0, cleared=0, cleared_deals=()):
        self.confirmed, self.cleared_count = Counter({"RING_LINK": confirmed}), Counter({"RING_LINK": cleared})
        self.cleared = set(cleared_deals)


def test_feedback_moves_levels_but_never_softens_old_tricks():
    warn, crit = finding("RING_LINK", "warning", "x", ["a"]), finding("RING_LINK", "critical", "x", ["a"])
    assert adjust(warn, Ctx(confirmed=3))["level"] == "critical"           # keeps being right -> stronger
    assert adjust(crit, Ctx(cleared=4))["level"] == "warning"              # keeps being a false alarm -> softer
    assert adjust(warn, Ctx(confirmed=1, cleared=1))["level"] == "warning"  # too little evidence to move
    assert adjust(crit, Ctx(cleared_deals=["a"]))["level"] == "info"       # only linked to cleared deals
    assert adjust(finding("STMT_TAMPER", "critical", "x"), Ctx(cleared=9))["level"] == "critical"


def test_proposed_pattern_is_stored_and_decisions_persist(tmp_path):
    e = engine(tmp_path, {"Ridgeway Freight Logistics", "Pecan Grove Catering", "Summit Peak Flooring"})
    e.advance("2d")
    proposed = [r for r in e.learned.values() if r["status"] == "proposed"]
    assert proposed and len(proposed[0]["tokens"]) >= 3
    e.decide_rule(proposed[0]["id"], "approve")
    assert e.store.list("rules")[0]["status"] == "active"
    e.decide_rule(proposed[0]["id"], "reject")
    assert e.store.list("rules")[0]["status"] == "rejected"
