from services.bootstrap import HISTORY_COLUMNS
from services.common.pocketbase import PocketBase
from services.stages import ORDER


COPIED = {
    "legal_name": "legal_name",
    "ein": "ein",
    "formation_date": "formation_date",
    "business_address": "business_address",
    "registered_agent": "registered_agent",
    "registered_agent_address": "registered_agent_address",
    "guarantor_name": "guarantor_name",
    "guarantor_dob": "guarantor_dob",
    "guarantor_dl_number": "guarantor_dl_number",
    "preparer_name": "preparer_name",
    "amount_requested": "amount_requested",
    "product": "product",
    "purpose": "purpose_category",
    "term_months": "term_months",
    "annual_revenue_stated": "annual_revenue_stated",
    "equipment_vendor": "equipment_vendor",
    "vendor_remit_acct_last4": "vendor_remit_acct_last4",
}


def snapshot(row: dict) -> dict:
    extracted = row.get("extracted") or {}
    fields = {column: "" for column in HISTORY_COLUMNS}
    fields["deal_id"] = row["folder"]
    fields["application_date"] = (row.get("entered_at") or "")[:10]
    fields["final_status"] = row["status"]
    for column, key in COPIED.items():
        value = extracted.get(key)
        if value not in (None, "", []):
            fields[column] = value if isinstance(value, str) else str(value)
    return fields


def run_once() -> None:
    pb = PocketBase()
    existing = {item["deal_id"] for item in pb.list_records("history")}
    for name in ORDER:
        for row in pb.list_records(name):
            if row.get("status") not in {"funded", "declined"}:
                continue
            if row["folder"] in existing:
                continue
            pb.create_record("history", snapshot(row))
            existing.add(row["folder"])
            print(f"history: recorded {row['folder']} as {row['status']}")


def main() -> None:
    import argparse
    import time

    parser = argparse.ArgumentParser(description="Append terminal deals onto the history table")
    parser.add_argument("--once", action="store_true")
    parser.add_argument("--interval", type=float, default=5)
    args = parser.parse_args()
    while True:
        run_once()
        if args.once:
            return
        time.sleep(args.interval)


if __name__ == "__main__":
    main()
