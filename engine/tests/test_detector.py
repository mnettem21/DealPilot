"""Detector tests. Run: python3 -m pytest -q tests/test_detector.py"""
import datetime as dt
from pathlib import Path

import pytest

from dealpilot.checks import check_deal
from dealpilot.detector import Context, screen
from dealpilot.extract import read_folder
from dealpilot.fraud import History
from dealpilot.models import StyleModel
from dealpilot.patterns import Patterns

DATA = next(p for p in (Path(__file__).resolve().parent.parent / "data", Path(__file__).resolve().parent.parent.parent) if (p / "shared_folder").exists())
RING = {"Ridgeway Freight Logistics", "Pecan Grove Catering", "Summit Peak Flooring"}
HIST = History(DATA)
PAT = Patterns(HIST)
STYLE = StyleModel(DATA)


@pytest.fixture(scope="module")
def deals():
    out = {}
    for d in sorted((DATA / "shared_folder").iterdir()):
        f, issues, notes = check_deal(read_folder(d), dt.date(2026, 11, 3), HIST)
        f["style"] = STYLE.score(f["dep_months"])
        out[d.name] = {"name": d.name, "facts": f, "issues": issues, "notes": notes}
    return out


def run(deals, names=None, extra=None):
    cur = {n: d["facts"] for n, d in deals.items() if names is None or n in names}
    cur.update({n: f for n, f in (extra or {}).items()})
    ctx = Context(HIST, PAT, cur)
    return {n: screen({"name": n, "facts": f, "issues": deals.get(n, {}).get("issues", []), "notes": deals.get(n, {}).get("notes", {})}, ctx)
            for n, f in cur.items()}


def rules(res):
    return {f["rule"] for f in res["findings"] if f["level"] != "info"}


def test_ring_is_a_warning_and_good_deals_stay_clear(deals):
    res = run(deals)
    assert {n for n, r in res.items() if r["level"] == "critical"} == {"Granite Bay Building Supply"}
    assert {n for n, r in res.items() if r["level"] == "warning"} == RING
    for n in RING:
        assert {"DEPENDENCY_BREAK", "RING_LINK", "PATTERN_NEIGHBOUR"} <= rules(res[n])
        assert {"H-2608-002", "H-2609-008"} <= set(res[n]["linked_deals"])


def test_old_tricks_fire_as_critical(deals):
    assert rules(run(deals)["Granite Bay Building Supply"]) == {"STMT_TAMPER", "REV_INFLATION"}


def test_lookalike_vendor_caught_by_learned_dependency(deals):
    brk = PAT.dependency_breaks(deals["Ridgeway Freight Logistics"]["facts"])
    assert brk and brk[0]["money"] and brk[0]["expected"] == "9902" and brk[0]["seen"] == "4471"
    assert not PAT.dependency_breaks(deals["Oakline Auto"]["facts"])  # the real Metroplex Inc. deal


def test_history_dependency_breaks_find_the_rest():
    assert {"H-2608-002", "H-2609-008"} <= set(PAT.history_breaks())
    assert PAT.neighbours({})[1] == [["H-2608-002", "H-2609-008"]]  # history alone: only the known ring pair, nothing else


def pair(deals, shared):
    """Two unrelated deals built only from popular values; they share just the columns named in `shared`."""
    out = {}
    for i, name in enumerate(("Alpha", "Beta")):
        f = {"legal_name": f"{name} Co LLC", "ein": f"{i + 1}{i + 1}-1111111", "business_address": f"{i + 1} Test St, Dallas, TX",
             "registered_agent": "Lone Star Registered Agents Inc", "preparer_name": "Rosa Benavides, EA",
             "preparer_firm": "Benavides Tax & Accounting", "bank_name": "Frost-Hill Bank", "product": "Term Loan", "term_months": 36,
             "amount_requested": 41000.0 + i * 913000, "formation_date": f"20{i + 1}0-0{i + 3}-15", "signed_date": "2026-11-05"}
        f.update(shared)
        out[name] = f
    return out


def test_brand_new_columns_need_no_code(deals):
    """Two deals sharing four columns the code has never heard of must be linked (two shared details are deliberately not enough)."""
    res = run(deals, names=[], extra=pair(deals, {"login_device": "dev-7731", "statement_footer": "rev-b-2.4",
                                                  "upload_ip": "10.4.4.9", "signature_font": "script-3"}))
    assert "PATTERN_NEIGHBOUR" in rules(res["Alpha"]) and "Beta" in res["Alpha"]["linked_deals"]


def test_two_shared_new_columns_alone_are_not_enough(deals):
    res = run(deals, names=[], extra=pair(deals, {"login_device": "dev-7731", "statement_footer": "rev-b-2.4"}))
    assert "PATTERN_NEIGHBOUR" not in rules(res["Alpha"])


@pytest.mark.parametrize("hidden", sorted(RING))
def test_leave_one_out(deals, hidden):
    res = run(deals, names=RING - {hidden})
    for n in RING - {hidden}:
        assert res[n]["level"] == "warning" and rules(res[n]) >= {"RING_LINK", "DEPENDENCY_BREAK"}


def test_first_arrival_alone_is_caught_through_history(deals):
    res = run(deals, names={"Ridgeway Freight Logistics"})["Ridgeway Freight Logistics"]
    assert res["level"] == "warning" and {"H-2608-002", "H-2609-008"} <= set(res["linked_deals"])
