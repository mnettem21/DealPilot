import csv

from services.common.pocketbase import PocketBase, PocketBaseError
from services.common.sources import contacts_source, history_csv


STAGES = ("intake", "credit", "committee", "product", "committed", "closed")

STAGE_FIELDS = (
    ("folder", "text", {"required": True, "min": 1, "max": 300}),
    ("entered_at", "text", {"required": True, "min": 1, "max": 80}),
    ("status", "text", {"required": True, "min": 1, "max": 40}),
    ("assignee", "text", {"max": 120}),
    ("extracted", "json", {"maxSize": 2000000}),
    ("waiting_on", "text", {"max": 5000}),
    ("memo", "text", {"max": 5000}),
)

HISTORY_COLUMNS = (
    "deal_id",
    "application_date",
    "legal_name",
    "ein",
    "industry",
    "naics",
    "formation_date",
    "business_address",
    "registered_agent",
    "registered_agent_address",
    "guarantor_name",
    "guarantor_dob",
    "guarantor_ssn_last4",
    "guarantor_dl_number",
    "preparer_name",
    "preparer_firm",
    "preparer_phone",
    "bank_name",
    "amount_requested",
    "product",
    "purpose",
    "term_months",
    "annual_revenue_stated",
    "equipment_vendor",
    "vendor_remit_acct_last4",
    "pdf_producer",
    "final_status",
    "fraud_label",
    "repayment_status",
    "relationship_since",
    "prior_note",
)


def text_field(style: str, name: str, required=False, minimum=0, maximum=5000) -> dict:
    if style == "fields":
        return {"name": name, "type": "text", "required": required, "min": minimum, "max": maximum}
    return {
        "name": name,
        "type": "text",
        "required": required,
        "options": {"min": minimum, "max": maximum},
    }


def json_field(style: str, name: str, max_size: int) -> dict:
    if style == "fields":
        return {"name": name, "type": "json", "maxSize": max_size}
    return {"name": name, "type": "json", "required": False, "options": {"maxSize": max_size}}


def build_fields(style: str, specs) -> list:
    fields = []
    for name, kind, options in specs:
        if kind == "json":
            fields.append(json_field(style, name, options.get("maxSize", 2000000)))
        else:
            fields.append(
                text_field(
                    style,
                    name,
                    required=options.get("required", False),
                    minimum=options.get("min", 0),
                    maximum=options.get("max", 5000),
                )
            )
    return fields


def collection_body(style: str, name: str, specs, indexes) -> dict:
    key = "fields" if style == "fields" else "schema"
    return {"name": name, "type": "base", key: build_fields(style, specs), "indexes": indexes}


def create_collection(pb: PocketBase, name: str, specs, indexes) -> None:
    try:
        pb.create_collection(collection_body("fields", name, specs, indexes))
    except PocketBaseError as exc:
        if exc.status != 400:
            raise
        pb.create_collection(collection_body("schema", name, specs, indexes))
    print(f"bootstrap: created {name}")


FRAUD_FIELDS = (
    ("folder", "text", {"required": True, "min": 1, "max": 300}),
    ("at", "text", {"required": True, "min": 1, "max": 80}),
    ("stage", "text", {"required": True, "min": 1, "max": 40}),
    ("reason", "text", {"required": True, "min": 1, "max": 5000}),
)


def ensure_fraud_queues(pb: PocketBase) -> None:
    names = {collection.get("name") for collection in pb.collections()}
    specs = (
        ("folder", "text", {"required": True, "min": 1, "max": 300}),
        ("at", "text", {"required": True, "min": 1, "max": 80}),
        ("stage", "text", {"max": 40}),
        ("reason", "text", {"max": 5000}),
        ("status", "text", {"required": True, "min": 1, "max": 40}),
        ("label", "text", {"max": 40}),
    )
    for name in ("fraud_critical", "fraud_warnings"):
        if name in names:
            continue
        create_collection(
            pb,
            name,
            specs,
            [f"CREATE UNIQUE INDEX `idx_{name}_folder` ON `{name}` (`folder`)"],
        )


def ensure_fraud_detected(pb: PocketBase) -> None:
    names = {collection.get("name") for collection in pb.collections()}
    if "fraud_detected" in names:
        return
    create_collection(
        pb,
        "fraud_detected",
        FRAUD_FIELDS,
        ["CREATE UNIQUE INDEX `idx_fraud_detected_folder` ON `fraud_detected` (`folder`)"],
    )


def wipe(pb: PocketBase) -> None:
    for collection in pb.collections():
        if collection.get("system"):
            continue
        name = collection.get("name", "")
        if name.startswith("_"):
            continue
        pb.delete_collection(collection["id"])
        print(f"bootstrap: removed {name}")


def seed_contacts(pb: PocketBase) -> None:
    for person in contacts_source():
        pb.create_record(
            "contacts",
            {
                "person_id": person["id"],
                "name": person.get("name") or "",
                "team": person.get("team") or "",
                "role": person.get("role") or "",
                "email": person.get("email") or "",
                "chat": person.get("chat") or "",
                "phone": person.get("phone") or "",
                "backup": person.get("backup") or "",
                "escalates_to": person.get("escalates_to") or "",
                "notes": person.get("notes") or "",
            },
        )
    print(f"bootstrap: loaded {len(contacts_source())} contacts")


def seed_history(pb: PocketBase) -> None:
    with history_csv().open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    for index, row in enumerate(rows, start=1):
        fields = {column: (row.get(column) or "") for column in HISTORY_COLUMNS}
        pb.create_record("history", fields)
        if index % 50 == 0:
            print(f"bootstrap: history {index}/{len(rows)}")
    print(f"bootstrap: loaded {len(rows)} history rows")


def main() -> None:
    pb = PocketBase()
    wipe(pb)
    for stage in STAGES:
        create_collection(
            pb,
            stage,
            STAGE_FIELDS,
            [f"CREATE UNIQUE INDEX `idx_{stage}_folder` ON `{stage}` (`folder`)"],
        )
    create_collection(
        pb,
        "files",
        (
            ("folder", "text", {"required": True, "max": 300}),
            ("path", "text", {"required": True, "max": 1000}),
            ("filename", "text", {"required": True, "max": 300}),
            ("arrived_at", "text", {"required": True, "max": 80}),
            ("doc_type", "text", {"max": 80}),
            ("source", "text", {"required": True, "max": 20}),
        ),
        ["CREATE UNIQUE INDEX `idx_files_path` ON `files` (`path`)"],
    )
    create_collection(
        pb,
        "actions",
        (
            ("at", "text", {"required": True, "max": 80}),
            ("folder", "text", {"required": True, "max": 300}),
            ("stage", "text", {"required": True, "max": 40}),
            ("actor", "text", {"required": True, "max": 120}),
            ("kind", "text", {"required": True, "max": 40}),
            ("decision", "text", {"max": 80}),
            ("why", "text", {"max": 5000}),
        ),
        [],
    )
    create_collection(
        pb,
        "alerts",
        (
            ("at", "text", {"required": True, "max": 80}),
            ("folder", "text", {"required": True, "max": 300}),
            ("stage", "text", {"required": True, "max": 40}),
            ("level", "text", {"required": True, "max": 20}),
            ("to", "text", {"required": True, "max": 120}),
            ("message", "text", {"required": True, "max": 5000}),
        ),
        ["CREATE UNIQUE INDEX `idx_alerts_once` ON `alerts` (`folder`, `stage`, `level`, `to`)"],
    )
    create_collection(
        pb,
        "contacts",
        (
            ("person_id", "text", {"required": True, "max": 120}),
            ("name", "text", {"max": 200}),
            ("team", "text", {"max": 120}),
            ("role", "text", {"max": 200}),
            ("email", "text", {"max": 200}),
            ("chat", "text", {"max": 120}),
            ("phone", "text", {"max": 40}),
            ("backup", "text", {"max": 120}),
            ("escalates_to", "text", {"max": 120}),
            ("notes", "text", {"max": 5000}),
        ),
        ["CREATE UNIQUE INDEX `idx_contacts_person` ON `contacts` (`person_id`)"],
    )
    history_specs = tuple(
        (column, "text", {"required": column == "deal_id", "max": 20000 if column == "prior_note" else 5000})
        for column in HISTORY_COLUMNS
    )
    create_collection(
        pb,
        "history",
        history_specs,
        ["CREATE UNIQUE INDEX `idx_history_deal` ON `history` (`deal_id`)"],
    )
    ensure_fraud_detected(pb)
    ensure_fraud_queues(pb)
    seed_contacts(pb)
    seed_history(pb)


if __name__ == "__main__":
    main()
