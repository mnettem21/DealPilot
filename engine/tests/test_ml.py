"""Tests for the ML pieces and the local-LLM guardrails. Run: python3 -m pytest -q tests/test_ml.py"""
import datetime as dt
from pathlib import Path

import pytest

from dealpilot import llm
from dealpilot.checks import check_deal
from dealpilot.extract import read_folder
from dealpilot.models import Forecast, StyleModel

DATA = next(p for p in (Path(__file__).resolve().parent.parent / "data", Path(__file__).resolve().parent.parent.parent) if (p / "shared_folder").exists())


def test_forecast_blocked_deals_finish_later():
    fc = Forecast(DATA)
    now = dt.datetime(2026, 11, 3, 12, tzinfo=dt.timezone(dt.timedelta(hours=-6)))
    later = [("Committee", 48), ("Product", 16), ("Committed", 48), ("Closed", 24)]
    free = fc.project("Credit", now - dt.timedelta(hours=10), 48, False, later, now)
    blocked = fc.project("Credit", now - dt.timedelta(hours=10), 48, True, later, now)
    assert blocked[0] > free[0] and blocked[1] >= blocked[0]
    assert fc.blocked_p50 > 1.0  # blocked stages always ran over the limit in history


def test_style_model_ranks_ring_among_most_unusual():
    sm = StyleModel(DATA)
    scores = {}
    for d in sorted((DATA / "shared_folder").iterdir()):
        f = check_deal(read_folder(d), dt.date(2026, 11, 3))[0]
        scores[d.name] = sm.score(f["dep_months"])["score"]
    top4 = sorted(scores, key=scores.get)[:4]
    assert {"Ridgeway Freight Logistics", "Pecan Grove Catering", "Summit Peak Flooring"} <= set(top4)


def test_llm_offline_returns_nothing(monkeypatch):
    monkeypatch.setattr(llm, "URL", "http://127.0.0.1:9")
    assert llm.ask("hi", timeout=1) is None
    assert llm.fill_fields(["ein"], "EIN 12-3456789") == {}


def test_llm_answers_are_validated(monkeypatch):
    monkeypatch.setattr(llm, "ask", lambda *a, **k: '{"ein": "ignore previous instructions", "amount_requested": 5000, '
                                                    '"product": "Free Money", "term_months": 36, "guarantor_name": "Jo 7"}')
    assert llm.fill_fields(["ein", "amount_requested", "product", "term_months", "guarantor_name"], "doc") == \
        {"amount_requested": 5000.0, "term_months": 36}
