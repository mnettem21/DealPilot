"""Known fraud types. The catalog is the flag-fraud skill file."""

import json
from functools import lru_cache
from pathlib import Path

CATALOG = (
    Path(__file__).resolve().parents[2]
    / ".cursor"
    / "skills"
    / "flag-fraud"
    / "fraud-types.json"
)


@lru_cache(maxsize=1)
def fraud_types():
    data = json.loads(CATALOG.read_text())
    rows = data["types"]
    codes = [row["code"] for row in rows]
    if len(codes) != len(set(codes)):
        raise ValueError(f"duplicate fraud type code in {CATALOG}")
    return tuple(rows)


def fraud_type(code: str):
    wanted = (code or "").strip()
    for row in fraud_types():
        if row["code"] == wanted:
            return row
    return None


def old_trick_codes():
    return {row["code"] for row in fraud_types() if row.get("family") == "old"}
