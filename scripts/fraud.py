#!/usr/bin/env python3
import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.bootstrap import ensure_fraud_detected
from services.common.clock import now
from services.common.fraud import fraud_for
from services.common.pocketbase import PocketBase
from services.common.records import find_deal


def main() -> None:
    parser = argparse.ArgumentParser(description="Flag a deal in fraud_detected")
    commands = parser.add_subparsers(dest="command", required=True)
    flag = commands.add_parser("flag")
    flag.add_argument("folder")
    flag.add_argument("reason")
    args = parser.parse_args()
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
        {"folder": args.folder, "at": now(), "stage": stage_name, "reason": args.reason},
    )
    print(f"fraud: flagged {args.folder} in {stage_name}")


if __name__ == "__main__":
    main()
