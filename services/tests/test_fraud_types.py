"""Catalog the flag-fraud skill is the list the backend reads."""

import json
import subprocess
import sys
from pathlib import Path

from services.common.fraud_types import fraud_type, fraud_types, old_trick_codes

ROOT = Path(__file__).resolve().parents[2]
CATALOG = ROOT / ".cursor" / "skills" / "flag-fraud" / "fraud-types.json"


def test_catalog_matches_the_skill_file():
    on_disk = json.loads(CATALOG.read_text())["types"]
    assert [row["code"] for row in fraud_types()] == [row["code"] for row in on_disk]
    assert old_trick_codes() == {
        "STMT_TAMPER",
        "REV_INFLATION",
        "ID_MISMATCH",
        "DUP_EIN",
        "SHELL_ADDRESS",
    }
    assert fraud_type("STYLE_ANOMALY")["writes_row"] is False
    assert fraud_type("STMT_TAMPER")["confirmed"] is True
    assert fraud_type("RING_LINK")["confirmed"] is False
    assert fraud_type("RING_LINK")["writes_row"] is False
    assert fraud_type("LEARNED_RULE")["confirmed"] is True


def test_flag_refuses_unknown_and_info_types():
    unknown = subprocess.run(
        [sys.executable, "scripts/fraud.py", "flag", "Cedar Loom", "--type", "NOT_A_TYPE", "--reason", "x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert unknown.returncode != 0
    assert "unknown fraud type NOT_A_TYPE" in unknown.stderr

    signal = subprocess.run(
        [sys.executable, "scripts/fraud.py", "flag", "Cedar Loom", "--type", "DOC_FORENSICS", "--reason", "x"],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )
    assert signal.returncode != 0
    assert "does not write a fraud_detected row" in signal.stderr
