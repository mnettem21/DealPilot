from .base import Stage, fill


ALLOWED_PRODUCTS = {
    "Term Loan",
    "Equipment Loan",
    "Revolving Line of Credit",
    "Working Capital Loan",
}


class Product(Stage):
    name = "product"
    next_name = "committed"
    sla_hours = 16
    doc_types = frozenset({"equipment_quote"})

    def assignee(self, extracted, contacts, now) -> str:
        return "kevin.alvarez"

    def absorb(self, extracted, parsed) -> dict:
        updated = super().absorb(extracted, parsed)
        facts = parsed.get("facts") or {}
        if parsed["document_type"] == "equipment_quote":
            fill(
                updated,
                {
                    "equipment_vendor": facts.get("equipment_vendor"),
                    "vendor_remit_acct_last4": facts.get("vendor_remit_acct_last4"),
                    "quote_total": facts.get("quote_total"),
                },
            )
        return updated

    def blockers(self, row) -> list:
        extracted = row.get("extracted") or {}
        docs = extracted.get("docs") or {}
        product = extracted.get("product") or ""
        purpose = extracted.get("purpose_category") or ""
        blockers = []
        if product not in ALLOWED_PRODUCTS:
            blockers.append("product not chosen")
        if purpose == "equipment" and product != "Equipment Loan":
            blockers.append("MISMATCH:product")
        if product == "Equipment Loan" and purpose and purpose != "equipment":
            blockers.append("MISMATCH:product")
        if product == "Equipment Loan" and not docs.get("equipment_quote"):
            blockers.append("MISSING_DOC:equipment_quote")
        quote_total = extracted.get("quote_total")
        amount = extracted.get("amount_requested")
        if quote_total and amount and float(quote_total) > float(amount):
            blockers.append("MISMATCH:amount_requested")
        return blockers


STAGE = Product()
