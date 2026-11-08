from services.common.fraud import fraud_for
from services.common.fraud_queue import committed_block, critical_status
from services.common.pocketbase import PocketBase
from services.common.records import latest_human_decision, log_action, move_row
from services.stages import load


WHY = {
    "intake": "Required intake documents are present and consistent",
    "credit": "Credit officer approved the request",
    "committee": "Committee approval received",
    "product": "Product and vendor requirements verified",
    "committed": "Commitment signoff approved",
}


def advance(pb, stage, row) -> None:
    destination = load(stage.next_name)
    move_row(pb, stage.name, row, destination, WHY[stage.name])


def fund(pb, stage, row) -> None:
    log_action(
        pb,
        row["folder"],
        stage.name,
        "rules",
        "decision",
        "funds disbursed",
        "Funding release approved",
    )
    pb.update_record(stage.name, row["id"], {"status": "funded", "waiting_on": ""})
    print(f"rules: {row['folder']} funded")


def consider(pb, stage, row) -> None:
    if row.get("status") in {"declined", "funded"}:
        return
    decision = latest_human_decision(pb, row["folder"], stage.name)
    if decision == "decline":
        pb.update_record(stage.name, row["id"], {"status": "declined", "waiting_on": ""})
        print(f"rules: {row['folder']} declined in {stage.name}")
        return
    blockers = stage.blockers(row)
    if stage.decision_type is None:
        if blockers:
            pb.update_record(stage.name, row["id"], {"status": "open", "waiting_on": "; ".join(blockers)})
        else:
            advance(pb, stage, row)
        return
    if decision == "request_info":
        waiting = "; ".join(blockers) if blockers else (row.get("waiting_on") or "Additional information requested")
        pb.update_record(stage.name, row["id"], {"status": "open", "waiting_on": waiting})
        return
    if blockers:
        pb.update_record(stage.name, row["id"], {"status": "open", "waiting_on": "; ".join(blockers)})
        return
    if decision == "approve":
        if stage.name == "committed":
            block = committed_block(critical_status(pb, row["folder"]))
            if block == "wait":
                pb.update_record(stage.name, row["id"], {"status": "waiting_human", "waiting_on": "fraud critical"})
                print(f"rules: {row['folder']} waiting at committed, fraud critical")
                return
            if block == "decline":
                pb.update_record(stage.name, row["id"], {"status": "declined", "waiting_on": "fraud critical"})
                print(f"rules: {row['folder']} rejected at committed by the fraud team")
                return
        if stage.next_name is None:
            if stage.name == "closed" and fraud_for(pb, row["folder"]):
                pb.update_record(stage.name, row["id"], {"waiting_on": "fraud detected"})
                print(f"rules: {row['folder']} funding held, fraud detected")
                return
            fund(pb, stage, row)
            return
        if stage.name == "credit":
            from services.rules.print_files import print_deal_files

            print_deal_files(pb, row["folder"])
        advance(pb, stage, row)
        return
    pb.update_record(
        stage.name,
        row["id"],
        {"status": "waiting_human", "waiting_on": stage.decision_type},
    )


def run_once(stage_name: str) -> None:
    stage = load(stage_name)
    pb = PocketBase()
    for row in pb.list_records(stage.name):
        consider(pb, stage, row)


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Apply one stage's exit rules")
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
