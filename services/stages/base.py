from services.common.people import active_person


def blank_extracted() -> dict:
    return {"docs": {}}


class Stage:
    name = ""
    next_name = None
    sla_hours = 24
    doc_types = frozenset()
    decision_type = None
    answers = frozenset()

    def sla_for(self, extracted: dict) -> int:
        return self.sla_hours

    def assignee(self, extracted: dict, contacts: dict, now: str) -> str:
        return ""

    def absorb(self, extracted: dict, parsed: dict) -> dict:
        updated = dict(extracted or {})
        docs = dict(updated.get("docs") or {})
        docs[parsed["document_type"]] = True
        updated["docs"] = docs
        return updated

    def blockers(self, row: dict) -> list:
        return []

    def memo(self, extracted: dict) -> str:
        return ""

    def enter_fields(self, extracted: dict, contacts: dict, now: str) -> dict:
        return {
            "entered_at": now,
            "status": "open",
            "assignee": self.assignee(extracted, contacts, now),
            "waiting_on": "",
            "memo": self.memo(extracted),
        }


def fill(extracted: dict, values: dict) -> None:
    for key, value in values.items():
        if value not in (None, "", []):
            extracted[key] = value


def active(person_id: str, contacts: dict, now: str) -> str:
    return active_person(person_id, contacts, now)
