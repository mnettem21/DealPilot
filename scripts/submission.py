#!/usr/bin/env python3
import argparse
import json
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from services.bootstrap import ensure_fraud_detected
from services.common.env import ROOT, load_env
from services.common.pocketbase import PocketBase
from services.stages import ORDER


JUDGING = {
    "intake": "Intake",
    "credit": "Credit",
    "committee": "Committee",
    "product": "Product",
    "committed": "Committed",
    "closed": "Closed",
}
REQUEST_TYPE = {
    "credit": "credit_decision",
    "committee": "committee_approval",
    "committed": "commitment_signoff",
    "closed": "funding_release",
}
EXTRACTED_FIELDS = (
    "legal_name",
    "ein",
    "amount_requested",
    "purpose_category",
    "product",
    "term_months",
    "guarantor_name",
    "annual_revenue_stated",
    "needed_by",
)
ISSUE_PREFIXES = ("MISSING_DOC:", "MISSING_PAGE:", "MISMATCH:", "OLD_TRICK:")


def judging(stage: str) -> str:
    return JUDGING.get(stage, stage)


def issues(waiting_on: str) -> list:
    found = []
    for part in (waiting_on or "").split(";"):
        code = part.strip()
        if code.startswith(ISSUE_PREFIXES):
            found.append(code)
    return found


def stage_log(actions: list, row: dict, stage_name: str, fraud: dict) -> list:
    by_stage = {}
    for action in actions:
        bucket = by_stage.setdefault(action["stage"], {"enter": None, "exit": None})
        if action["kind"] == "stage_enter" and bucket["enter"] is None:
            bucket["enter"] = action
        elif action["kind"] == "stage_exit" and bucket["exit"] is None:
            bucket["exit"] = action
    log = []
    for stage in ORDER:
        bucket = by_stage.get(stage)
        if not bucket or not bucket["enter"]:
            continue
        exit_action = bucket["exit"]
        entry = {
            "stage": judging(stage),
            "entered_at": bucket["enter"]["at"],
            "exited_at": exit_action["at"] if exit_action else None,
            "decision": exit_action.get("decision") if exit_action else None,
            "why": exit_action.get("why") if exit_action else None,
        }
        if stage == stage_name and not exit_action:
            if row.get("status") == "declined":
                decline = next(
                    (
                        action
                        for action in actions
                        if action["kind"] == "decision"
                        and action.get("decision") == "decline"
                        and action.get("actor") != "rules"
                    ),
                    None,
                )
                entry["decision"] = "declined"
                entry["why"] = decline.get("why") if decline else None
            elif stage == "closed" and row.get("status") == "funded":
                funded = next((action for action in actions if action.get("decision") == "funds disbursed"), None)
                entry["decision"] = "funds disbursed"
                entry["why"] = funded.get("why") if funded else "Funding release approved"
            elif stage == "closed" and fraud and row.get("status") != "funded":
                entry["decision"] = "held"
                entry["why"] = fraud.get("reason")
        log.append(entry)
    return log


def human_requests(actions: list) -> list:
    entered = {}
    for action in actions:
        if action["kind"] == "stage_enter" and action["stage"] not in entered:
            entered[action["stage"]] = action["at"]
    requests = []
    for action in actions:
        if action["kind"] != "decision" or action.get("actor") == "rules":
            continue
        if action.get("decision") not in {"approve", "decline", "request_info"}:
            continue
        if action["stage"] not in REQUEST_TYPE:
            continue
        requests.append(
            {
                "type": REQUEST_TYPE[action["stage"]],
                "to": action.get("actor"),
                "at": entered.get(action["stage"]),
                "message": action.get("why") or "",
                "answer": action.get("decision"),
                "answered_at": action.get("at"),
            }
        )
    return requests


def fraud_block(row: dict, stage_name: str, fraud: dict) -> dict:
    if not fraud:
        return {
            "flagged": False,
            "flagged_at": None,
            "stage_at_flag": None,
            "held": False,
            "reason": None,
            "linked_deals": [],
        }
    return {
        "flagged": True,
        "flagged_at": fraud.get("at"),
        "stage_at_flag": judging(fraud.get("stage") or ""),
        "held": stage_name == "closed" and row.get("status") != "funded",
        "reason": fraud.get("reason"),
        "linked_deals": [],
    }


def build(set_name: str) -> dict:
    load_env()
    pb = PocketBase()
    ensure_fraud_detected(pb)
    deals = []
    for stage in ORDER:
        for row in pb.list_records(stage):
            deals.append((stage, row))
    actions = {}
    for action in pb.list_records("actions", sort="id"):
        actions.setdefault(action["folder"], []).append(action)
    alerts = {}
    for alert in pb.list_records("alerts", sort="id"):
        alerts.setdefault(alert["folder"], []).append(alert)
    flags = {row["folder"]: row for row in pb.list_records("fraud_detected")}
    payload = {"team": os.environ.get("DEALPILOT_TEAM") or None, "set": set_name, "deals": {}, "history_flags": []}
    for stage_name, row in deals:
        folder = row["folder"]
        extracted = row.get("extracted") or {}
        deal_actions = actions.get(folder, [])
        fraud = flags.get(folder)
        payload["deals"][folder] = {
            "extracted": {field: extracted.get(field) if extracted.get(field) not in ("", None) else None for field in EXTRACTED_FIELDS},
            "issues": issues(row.get("waiting_on") or ""),
            "stage_log": stage_log(deal_actions, row, stage_name, fraud),
            "human_requests": human_requests(deal_actions),
            "alerts": [
                {
                    "at": alert.get("at"),
                    "stage": judging(alert.get("stage") or ""),
                    "level": alert.get("level"),
                    "to": alert.get("to"),
                    "message": alert.get("message"),
                }
                for alert in alerts.get(folder, [])
            ],
            "fraud": fraud_block(row, stage_name, fraud),
        }
    return payload


def main() -> None:
    parser = argparse.ArgumentParser(description="Write the DealPilot submission JSON")
    parser.add_argument("--set", default="sample")
    parser.add_argument("--out", default=str(ROOT / "output" / "submission.json"))
    args = parser.parse_args()
    payload = build(args.set)
    destination = Path(args.out)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps(payload, indent=2) + "\n")
    print(f"submission: wrote {len(payload['deals'])} deals to {destination}")


if __name__ == "__main__":
    main()
