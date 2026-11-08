from .clock import now
from .people import load_contacts
from .pocketbase import escape_filter


def log_action(pb, folder: str, stage: str, actor: str, kind: str, decision: str = "", why: str = "") -> dict:
    return pb.create_record(
        "actions",
        {
            "at": now(),
            "folder": folder,
            "stage": stage,
            "actor": actor,
            "kind": kind,
            "decision": decision,
            "why": why,
        },
    )


def latest_human_decision(pb, folder: str, stage: str):
    expr = (
        f"folder='{escape_filter(folder)}' && stage='{escape_filter(stage)}' && kind='decision'"
    )
    rows = pb.list_records("actions", expr, sort="-id")
    for row in rows:
        if row.get("actor") == "rules":
            continue
        if row.get("decision") in {"approve", "decline", "request_info"}:
            return row["decision"]
    return None


def find_deal(pb, folder: str):
    from services.stages import ORDER

    expr = f"folder='{escape_filter(folder)}'"
    found = []
    for name in ORDER:
        rows = pb.list_records(name, expr)
        found.extend((name, row) for row in rows)
    return found


def move_row(pb, source_name: str, row: dict, destination, why: str) -> dict:
    contacts = load_contacts(pb)
    extracted = row.get("extracted") or {}
    fields = destination.enter_fields(extracted, contacts, now())
    fields["folder"] = row["folder"]
    fields["extracted"] = extracted
    created = pb.create_record(destination.name, fields)
    try:
        log_action(pb, row["folder"], source_name, "rules", "stage_exit", "advanced", why)
        log_action(pb, row["folder"], destination.name, "rules", "stage_enter", "entered", why)
        pb.delete_record(source_name, row["id"])
    except Exception:
        pb.delete_record(destination.name, created["id"])
        raise
    print(f"rules: {row['folder']} {source_name} -> {destination.name}")
    return created
