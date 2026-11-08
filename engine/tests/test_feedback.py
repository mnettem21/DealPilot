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


def _at_committed(deal):
    deal["stage"] = "Committed"
    deal["human_requests"].append({
        "type": "commitment_signoff", "answer": "approve", "stage": "Committed", "consumed": False,
    })


def test_unconfirmed_pattern_warns_without_a_hold(tmp_path):
    e = engine(tmp_path, {"Ridgeway Freight Logistics"})
    e.advance("1d")
    d = e.state["deals"]["Ridgeway Freight Logistics"]
    assert d["fraud"]["level"] == "warning" and d["fraud"].get("warned") and not d["fraud"].get("held")
    assert d["status"] == "active"
    warnings = [row for row in e.store.list("fraud_warnings") if row["folder"] == d["name"]]
    assert warnings and not e.store.list("fraud_critical")
    assert not any(r["type"] == "fraud_hold" for r in d["human_requests"])
    _at_committed(d)
    e._do_committed(d, e.now())
    assert d.get("waiting_on") != "fraud critical"


def test_confirmed_fraud_is_queued_and_waits_only_at_committed(tmp_path):
    e = engine(tmp_path, {"Granite Bay Building Supply"})
    e.advance("2d")
    d = e.state["deals"]["Granite Bay Building Supply"]
    assert d["fraud"]["level"] == "critical" and not d["fraud"].get("held") and d["status"] == "active"
    assert d["stage"] != "Closed"
    row = next(r for r in e.store.list("fraud_critical") if r["folder"] == d["name"])
    assert row["label"] == "fraud critical" and row["status"] == "open"
    _at_committed(d)
    e._do_committed(d, e.now())
    assert d["stage"] == "Committed" and d["waiting_on"] == "fraud critical"
    e.decide_fraud(d["name"], "release")
    e._do_committed(d, e.now())
    assert d["waiting_on"] != "fraud critical"
    assert e.store.list("fraud_critical")[0]["status"] == "released"


def test_fraud_team_reject_stops_the_deal_at_committed(tmp_path):
    e = engine(tmp_path, {"Granite Bay Building Supply"})
    e.advance("2d")
    d = e.state["deals"]["Granite Bay Building Supply"]
    _at_committed(d)
    e.decide_fraud(d["name"], "reject")
    e._do_committed(d, e.now())
    assert d["status"] == "declined" and d["stage"] == "Committed"


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
