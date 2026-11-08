import re
from datetime import datetime


def active_person(person_id: str, contacts: dict, now: str) -> str:
    person = contacts.get(person_id)
    if not person:
        return person_id
    match = re.search(r"OUT OF OFFICE ([0-9:T -]+) through ([0-9:T -]+)", person.get("notes") or "")
    if not match:
        return person_id
    current = datetime.fromisoformat(now)
    if current.tzinfo:
        current = current.replace(tzinfo=None)
    start = datetime.fromisoformat(match.group(1).strip())
    end = datetime.fromisoformat(match.group(2).strip())
    if start <= current <= end:
        return person.get("backup") or person_id
    return person_id


def load_contacts(pb) -> dict:
    return {row["person_id"]: row for row in pb.list_records("contacts")}
