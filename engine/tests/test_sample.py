"""Sample-week expectations. Run: python3 -m pytest -q"""
import datetime as dt
import json
from pathlib import Path

import pytest

from dealpilot.checks import check_deal
from dealpilot.extract import read_folder
from dealpilot.fraud import History, Linker

DATA = next(p for p in (Path(__file__).resolve().parent.parent / "data", Path(__file__).resolve().parent.parent.parent) if (p / "shared_folder").exists())
HIST = History(DATA)


@pytest.fixture(scope="module")
def deals():
    out = {}
    for d in sorted((DATA / "shared_folder").iterdir()):
        facts, issues, _ = check_deal(read_folder(d), dt.date(2026, 11, 3), HIST)
        out[d.name] = (facts, issues)
    return out


def test_read_facts(deals):
    f, _ = deals["Priya's Bakery"]
    assert (f["legal_name"], f["amount_requested"], f["purpose_category"]) == ("Sunrise Rasoi Bakery LLC", 35000, "payroll")
    assert f["needed_by"] == "2026-11-06T15:00-06:00"
    assert deals["tyler loopwise loan stuff"][0]["guarantor_name"] == "Tyler James Brooks"


def test_read_issues(deals):
    assert "MISMATCH:ein" in deals["Westfield Print & Sign"][1]
    assert "MISSING_DOC:bank_statement_2026-06" in deals["tyler loopwise loan stuff"][1]
    assert "MISSING_PAGE:bank_statement_2026-08" in deals["Oakline Auto"][1]
    assert "MISSING_DOC:tax_return" in deals["LSFD_docs"][1]
    assert not any(i.startswith("MISMATCH:guarantor") for i in deals["Oakline Auto"][1])  # Bob == Robert


def test_ring_found_and_good_deals_left_alone(deals):
    rings = Linker(HIST).run({n: f for n, (f, _) in deals.items()})
    assert set(rings) == {"Ridgeway Freight Logistics", "Pecan Grove Catering", "Summit Peak Flooring"}
    assert {"H-2608-002", "H-2609-008"} <= set(rings["Ridgeway Freight Logistics"]["linked"])


def test_history_has_no_false_rings():
    flagged = Linker(HIST).run({}, report_history=True)
    assert set(flagged) == {"H-2608-002", "H-2609-008"}


def test_submission_shape():
    p = Path(__file__).resolve().parent.parent / "submission.json"
    if not p.exists():
        pytest.skip("run python3 -m dealpilot.cli run first")
    s = json.load(open(p))
    assert {"team", "set", "deals", "history_flags"} <= set(s)
    for d in s["deals"].values():
        assert {"extracted", "issues", "stage_log", "human_requests", "alerts", "fraud"} <= set(d)
    assert s["history_flags"] == ["H-2608-002", "H-2609-008"]
