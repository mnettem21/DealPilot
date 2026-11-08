import json
from datetime import datetime

from services.common.clock import now
from services.common.fraud import fraud_for
from services.common.people import load_contacts
from services.common.pocketbase import PocketBase, PocketBaseError, escape_filter
from services.stages import load


def run_once(stage_name: str) -> None:
    stage = load(stage_name)
    pb = PocketBase()
    contacts = load_contacts(pb)
    current = datetime.fromisoformat(now())
    for row in pb.list_records(stage.name):
        if row.get("status") in {"declined", "funded"}:
            continue
        entered = datetime.fromisoformat(row["entered_at"])
        elapsed = (current - entered).total_seconds() / 3600
        extracted = row.get("extracted") or {}
        limit = stage.sla_for(extracted)
        owner = row.get("assignee") or stage.assignee(extracted, contacts, now())
        manager = extracted.get("relationship_manager") or ""
        fraud = fraud_for(pb, row["folder"]) if stage.name == "closed" else None
        if fraud:
            reason = fraud.get("reason") or "fraud detected"
            waiting = f"fraud is holding it up: {reason}"
        else:
            waiting = row.get("waiting_on") or "stage review"
        notices = []
        if elapsed >= limit:
            notices.append(("alert", owner))
            if manager and manager != owner:
                notices.append(("alert", manager))
        if elapsed >= limit * 1.5:
            manager_of_owner = (contacts.get(owner) or {}).get("escalates_to") or "lauren.kim"
            notices.append(("escalation", manager_of_owner))
        if elapsed >= limit * 2:
            notices.append(("escalation", "lauren.kim"))
        for level, recipient in notices:
            message = (
                f"{row['folder']} (RM: {manager or 'unassigned'}) has been in {stage.name} "
                f"for {elapsed:.1f} hours. Waiting on {waiting}. Resolve that to unblock it."
            )
            try:
                pb.create_record(
                    "alerts",
                    {
                        "at": now(),
                        "folder": row["folder"],
                        "stage": stage.name,
                        "level": level,
                        "to": recipient,
                        "message": message,
                    },
                )
                print(f"sla: {level} {row['folder']} -> {recipient}")
            except PocketBaseError as exc:
                if exc.status == 400 and "unique" in exc.detail.lower():
                    if fraud:
                        expr = (
                            f"folder='{escape_filter(row['folder'])}' && stage='{escape_filter(stage.name)}' "
                            f"&& level='{escape_filter(level)}' && to='{escape_filter(recipient)}'"
                        )
                        existing = pb.list_records("alerts", expr)
                        if existing and existing[0].get("message") != message:
                            pb.update_record("alerts", existing[0]["id"], {"message": message, "at": now()})
                            print(f"sla: updated {level} {row['folder']} -> {recipient}")
                    continue
                raise


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Watch one stage for SLA breaches")
    parser.add_argument("--stage", required=True)
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    while True:
        run_once(args.stage)
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
