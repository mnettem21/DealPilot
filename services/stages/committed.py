from .base import Stage


class Committed(Stage):
    name = "committed"
    next_name = "closed"
    sla_hours = 48
    decision_type = "commitment_signoff"
    answers = frozenset({"approve", "decline"})

    def assignee(self, extracted, contacts, now) -> str:
        return extracted.get("relationship_manager") or "brian.foster"


STAGE = Committed()
