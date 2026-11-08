"""Stress tests: invented tricks (not in the sample) and look-alike innocents. Run: python3 -m pytest -q tests/test_stress.py"""
import datetime as dt
from pathlib import Path

import pytest

from dealpilot.checks import check_deal
from dealpilot.extract import read_folder
from dealpilot.fraud import History, Linker

DATA = next(p for p in (Path(__file__).resolve().parent.parent / "data", Path(__file__).resolve().parent.parent.parent) if (p / "shared_folder").exists())
HIST = History(DATA)


@pytest.fixture(scope="module")
def base():
    out = {}
    for d in sorted((DATA / "shared_folder").iterdir()):
        out[d.name] = check_deal(read_folder(d), dt.date(2026, 11, 3), HIST)[0]
    return out


def synth(i, **kw):
    f = {"legal_name": f"Synthetic Co {i} LLC", "ein": f"10-00000{i:02d}", "business_address": f"{100+i} Test Rd, Dallas, TX",
         "registered_agent": "Lone Star Registered Agents Inc", "preparer_name": "Rosa Benavides, EA",
         "preparer_firm": "Benavides Tax & Accounting", "bank_name": "Frost-Hill Bank", "pdf_producer": "Skia/PDF m124",
         "amount_requested": 80000.0, "formation_date": "2015-01-01", "signed_date": "2026-11-05", "phone": f"214-555-02{i:02d}",
         "guarantor_address": f"{200+i} Home St, Dallas, TX", "guarantor_dl": f"5000{i:04d}", "guarantor_ssn4": f"{1000+i}",
         "guarantor_dob": f"1980-01-{i+1:02d}", "statement_producers": ["FIS Statement Services 9.2"],
         "round_deposit_share": 0.02, "outbound_transfer_accts": []}
    f.update(kw)
    return f


def run(base, *extra):
    cur = dict(base)
    cur.update({f"X{i}": f for i, f in enumerate(extra)})
    return Linker(HIST).run(cur)


def test_new_trick_shared_phone_and_home_address(base):
    r = run(base, synth(1, phone="469-555-0999", guarantor_address="9 Shared Ln, Plano, TX"),
            synth(2, phone="469-555-0999", guarantor_address="9 Shared Ln, Plano, TX"))
    assert {"X0", "X1"} <= set(r)


def test_synthetic_identity_same_license_different_names(base):
    r = run(base, synth(3, guarantor_dl="77123456"), synth(4, guarantor_dl="77123456", guarantor_ssn4="1003"))
    assert {"X0", "X1"} <= set(r)


def test_statement_style_ring_without_vendor(base):
    kw = dict(statement_producers=["PixelBank Gen 1.0"], outbound_transfer_accts=["5512"], round_deposit_share=0.95)
    r = run(base, synth(5, **kw), synth(6, **kw), synth(7, **kw))
    assert {"X0", "X1", "X2"} <= set(r)


def test_single_rare_attribute_is_not_enough(base):
    r = run(base, synth(8, preparer_name="Z. Rare", preparer_firm="Rare Books LLC"),
            synth(9, preparer_name="Z. Rare", preparer_firm="Rare Books LLC"))
    assert "X0" not in r and "X1" not in r


def test_popular_attributes_never_link(base):
    r = run(base, synth(10), synth(11), synth(12))
    assert not any(k.startswith("X") for k in r)


def test_returning_borrower_is_not_a_ring(base):
    again = dict(base["Garza Family Pharmacy"])
    r = run(base, again | {"legal_name": "Garza Family Pharmacy LLC"})
    assert "X0" not in r


def test_real_sample_unchanged_by_noise(base):
    r = run(base, *[synth(i) for i in range(20, 40)])
    assert {k for k in r if not k.startswith("X")} == {"Ridgeway Freight Logistics", "Pecan Grove Catering", "Summit Peak Flooring"}


def test_disclosed_affiliated_owner_is_not_flagged(base):
    r = run(base, synth(13, guarantor_dl="88123456", affiliated="Synthetic Co 14 LLC"),
            synth(14, guarantor_dl="88123456", affiliated="Synthetic Co 13 LLC"))
    assert "X0" not in r and "X1" not in r
