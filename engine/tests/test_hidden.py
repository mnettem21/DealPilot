"""End-to-end check. Sample week always runs. For a hidden/weekend drop: HIDDEN_DIR=<participant folder> [HIDDEN_KEY=key.json]
python3 -m pytest -q tests/test_hidden.py"""
import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
CASES = [(str(next(p for p in (ROOT / "data", ROOT.parent) if (p / "shared_folder").exists())), str(ROOT / "tests" / "expected_sample.json"))]
if os.environ.get("HIDDEN_DIR"):
    CASES.append((os.environ["HIDDEN_DIR"], os.environ.get("HIDDEN_KEY")))


@pytest.mark.parametrize("data,key", CASES)
def test_week(data, key, tmp_path):
    cmd = [sys.executable, "-m", "dealpilot.cli", "check", "--data", data, "--state", str(tmp_path / "s.json"), "--out", str(tmp_path / "o.json")]
    r = subprocess.run(cmd + (["--expect", key] if key else []), cwd=ROOT, capture_output=True, text=True)
    assert r.returncode == 0, r.stdout[-3000:] + r.stderr[-1500:]
