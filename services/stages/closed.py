from .base import Stage


class Closed(Stage):
    name = "closed"
    next_name = None
    sla_hours = 24
    decision_type = "funding_release"
    answers = frozenset({"approve", "decline"})

    def assignee(self, extracted, contacts, now) -> str:
        return "tasha.reed"


STAGE = Closed()
