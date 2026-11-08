from .base import Stage


DELEGATED_MAX = 249999.99


class Committee(Stage):
    name = "committee"
    next_name = "product"
    sla_hours = 48
    decision_type = "committee_approval"
    answers = frozenset({"approve", "decline"})

    def sla_for(self, extracted) -> int:
        amount = float(extracted.get("amount_requested") or 0)
        return 24 if amount <= DELEGATED_MAX else 48

    def assignee(self, extracted, contacts, now) -> str:
        amount = float(extracted.get("amount_requested") or 0)
        return "priscilla.nguyen" if amount <= DELEGATED_MAX else "harold.benson"

    def memo(self, extracted) -> str:
        name = extracted.get("legal_name") or "The borrower"
        amount = extracted.get("amount_requested") or 0
        purpose = extracted.get("purpose_category") or "the stated purpose"
        product = extracted.get("product") or "the requested product"
        term = extracted.get("term_months") or "n/a"
        return (
            f"{name} requests ${amount} for {purpose}. "
            f"Product {product}, term {term} months."
        )

    def blockers(self, row) -> list:
        if not str(row.get("memo") or "").strip():
            return ["credit memo"]
        return []


STAGE = Committee()
