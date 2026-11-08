from pathlib import Path

from services.common.clock import arrived, now
from services.common.env import ROOT
from services.common.people import load_contacts
from services.common.pocketbase import PocketBase
from services.common.records import find_deal, log_action
from services.common.sources import assignments
from services.parser.extract import process_document
from services.stages import load


def ensure_intake(pb: PocketBase, arrivals) -> None:
    stage = load("intake")
    contacts = load_contacts(pb)
    assigned = assignments()
    seen = set()
    for item in arrivals:
        folder = item["path"].split("/", 1)[0]
        if folder in seen or find_deal(pb, folder):
            seen.add(folder)
            continue
        seen.add(folder)
        extracted = {"docs": {}}
        pair = assigned.get(folder) or {}
        if pair.get("relationship_manager"):
            extracted["relationship_manager"] = pair["relationship_manager"]
        if pair.get("credit_officer"):
            extracted["credit_officer"] = pair["credit_officer"]
        moment = now()
        pb.create_record(
            "intake",
            {
                "folder": folder,
                "entered_at": moment,
                "status": "open",
                "assignee": stage.assignee(extracted, contacts, moment),
                "extracted": extracted,
                "waiting_on": "",
                "memo": "",
            },
        )
        log_action(pb, folder, "intake", "parser", "stage_enter", "entered", "First file arrived")
        print(f"intake parser: opened {folder}")


def run_once(stage_name: str) -> None:
    stage = load(stage_name)
    pb = PocketBase()
    arrivals = arrived()
    if stage.name == "intake":
        ensure_intake(pb, arrivals)
    known = {item["path"] for item in pb.list_records("files")}
    for row in pb.list_records(stage.name):
        extracted = dict(row.get("extracted") or {})
        changed = False
        prefix = row["folder"] + "/"
        for item in arrivals:
            if not item["path"].startswith(prefix):
                continue
            relative = "shared_folder/" + item["path"]
            if relative in known:
                continue
            full = ROOT / relative
            if not full.exists():
                print(f"{stage.name} parser: missing {relative}")
                continue
            try:
                parsed = process_document(full)
            except Exception as exc:
                print(f"{stage.name} parser: skipped {relative}: {exc}")
                continue
            if parsed["document_type"] not in stage.doc_types:
                continue
            pb.create_record(
                "files",
                {
                    "folder": row["folder"],
                    "path": relative,
                    "filename": Path(relative).name,
                    "arrived_at": item["arrive_at"],
                    "doc_type": parsed["document_type"],
                    "source": "shared",
                },
            )
            known.add(relative)
            extracted = stage.absorb(extracted, parsed)
            changed = True
            print(f"{stage.name} parser: {row['folder']} {parsed['document_type']} {Path(relative).name}")
        if changed:
            pb.update_record(stage.name, row["id"], {"extracted": extracted})


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Read arrived shared-folder files for one stage")
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
