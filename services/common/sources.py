import json
from pathlib import Path

from .env import ROOT


def assignments() -> dict:
    path = ROOT / "sim" / "assignments.json"
    return json.loads(path.read_text())


def contacts_source() -> list:
    path = ROOT / "contacts" / "teams.json"
    return json.loads(path.read_text())["people"]


def history_csv() -> Path:
    return ROOT / "history" / "deals.csv"
