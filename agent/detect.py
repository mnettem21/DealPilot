"""Screen one mounted deal and return warning or critical JSON.

Follow agent/INSTRUCTIONS.md. Confirmed catalog types are critical.
A new pattern, including the Metroplex lookalike vendor, is a warning and does not hold.
"""

import datetime as dt
import json
import os
import sys
import urllib.request
from pathlib import Path

ENGINE = Path(__file__).resolve().parents[1] / "engine"
if str(ENGINE) not in sys.path:
    sys.path.insert(0, str(ENGINE))

from dealpilot.checks import check_deal
from dealpilot.detector import Context, screen
from dealpilot.extract import read_folder
from dealpilot.fraud import History
from dealpilot.models import StyleModel
from dealpilot.patterns import Patterns

_MODELS = {}


def catalog_types():
    for parent in Path(__file__).resolve().parents:
        path = parent / ".cursor" / "skills" / "flag-fraud" / "fraud-types.json"
        if path.is_file():
            return json.loads(path.read_text())["types"]
    raise FileNotFoundError("fraud-types.json")


def confirmed_codes():
    return {row["code"] for row in catalog_types() if row.get("confirmed")}


def _models(data_dir):
    key = str(Path(data_dir).resolve())
    if key not in _MODELS:
        hist = History(data_dir)
        _MODELS[key] = (hist, Patterns(hist), StyleModel(data_dir))
    return _MODELS[key]


def screen_folder(deal_dir, data_dir, folder, ref=dt.date(2026, 11, 3)):
    """Read one deal folder and score it against history. One deal is enough."""
    hist, patterns, style = _models(data_dir)
    facts, issues, notes = check_deal(read_folder(deal_dir), ref, hist)
    scored = style.score(facts.get("dep_months"))
    if scored:
        facts["style"] = scored
    ctx = Context(hist, patterns, {folder: facts})
    raw = screen({"name": folder, "facts": facts, "issues": issues, "notes": notes}, ctx)
    return apply_policy(folder, facts, raw, confirmed_codes())


def apply_policy(folder, facts, raw, confirmed):
    findings = []
    for item in raw["findings"]:
        if item["level"] == "info":
            continue
        is_confirmed = item["rule"] in confirmed
        findings.append(
            {
                "rule": item["rule"],
                "level": "critical" if is_confirmed else "warning",
                "confirmed": is_confirmed,
                "evidence": item["evidence"],
            }
        )
    if any(item["level"] == "critical" for item in findings):
        level = "critical"
    elif findings:
        level = "warning"
    else:
        level = "none"
    return {
        "folder": folder,
        "level": level,
        "hold": False,
        "findings": findings,
        "linked_deals": raw.get("linked_deals") or [],
        "signals": {
            "vendor": facts.get("vendor"),
            "remit_acct_last4": facts.get("remit_acct_last4"),
        },
        "reason": " ".join(f"[{item['rule']}] {item['evidence']}" for item in findings),
    }


def publish(payload):
    text = json.dumps(payload)
    print(text)
    url = os.environ.get("PIPELINE_URL", "").strip()
    if not url:
        return
    headers = {"Content-Type": "application/json"}
    token = os.environ.get("API_TOKEN", "").strip()
    if token:
        headers["Authorization"] = f"Bearer {token}"
    request = urllib.request.Request(url, text.encode(), headers)
    with urllib.request.urlopen(request, timeout=30) as response:
        if response.status >= 300:
            raise SystemExit(f"pipeline returned {response.status}")


def main():
    deal_dir = Path(os.environ.get("DEAL_DIR", "/data"))
    data_dir = Path(os.environ.get("DATA_DIR", Path(__file__).resolve().parents[1]))
    folder = os.environ.get("DEAL_FOLDER") or deal_dir.name
    if not deal_dir.is_dir():
        raise SystemExit(f"deal directory not found: {deal_dir}")
    publish(screen_folder(deal_dir, data_dir, folder))


if __name__ == "__main__":
    main()
