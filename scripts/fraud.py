#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.bootstrap import ensure_fraud_detected
from services.common.clock import now
from services.common.fraud import fraud_for
from services.common.fraud_types import fraud_type, fraud_types
from services.common.pocketbase import PocketBase
from services.common.records import find_deal


def _reason(code, text):
    detail = (text or "").strip()
    if not code:
        if not detail:
            raise SystemExit("reason is required")
        return detail
    spec = fraud_type(code)
    if spec is None:
        known = ", ".join(row["code"] for row in fraud_types())
        raise SystemExit(f"unknown fraud type {code}. Known types: {known}")
    if not spec["writes_row"]:
        raise SystemExit(f"{spec['code']} does not write a fraud_detected row")
    return f"{spec['code']}: {detail or spec['name']}"


def main() -> None:
    parser = argparse.ArgumentParser(description="Flag a deal in fraud_detected")
    commands = parser.add_subparsers(dest="command", required=True)
    flag = commands.add_parser("flag")
    flag.add_argument("folder")
    flag.add_argument("reason", nargs="?", default="")
    flag.add_argument("--type", dest="fraud_type")
    flag.add_argument("--reason", dest="reason_flag", default="")
    args = parser.parse_args()
    reason = _reason(args.fraud_type, args.reason_flag or args.reason)
    pb = PocketBase()
    ensure_fraud_detected(pb)
    if fraud_for(pb, args.folder):
        print(f"fraud: {args.folder} is already flagged")
        return
    found = find_deal(pb, args.folder)
    if not found:
        raise SystemExit(f"{args.folder} is not in a stage queue")
    stage_name = found[0][0]
    pb.create_record(
        "fraud_detected",
        {"folder": args.folder, "at": now(), "stage": stage_name, "reason": reason},
    )
    print(f"fraud: flagged {args.folder} in {stage_name}")


if __name__ == "__main__":
    main()
