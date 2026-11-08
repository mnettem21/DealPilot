from datetime import datetime, timedelta

from .base import Stage, active, fill


REQUIRED_MONTHS = ("2026-05", "2026-06", "2026-07", "2026-08", "2026-09", "2026-10")


def _young(formation_date: str, now: str) -> bool:
    if not formation_date:
        return False
    try:
        formed = datetime.fromisoformat(str(formation_date)[:10])
        current = datetime.fromisoformat(now)
    except ValueError:
        return False
    if current.tzinfo:
        current = current.replace(tzinfo=None)
    return formed > current - timedelta(days=365 * 2)


class Credit(Stage):
    name = "credit"
    next_name = "committee"
    sla_hours = 48
    doc_types = frozenset({"bank_statement", "financial_statements", "tax_return", "tax_extension"})
    decision_type = "credit_decision"
    answers = frozenset({"approve", "decline", "request_info"})

    def assignee(self, extracted, contacts, now) -> str:
        officer = extracted.get("credit_officer") or "marcus.lee"
        return active(officer, contacts, now)

    def absorb(self, extracted, parsed) -> dict:
        updated = super().absorb(extracted, parsed)
        facts = parsed.get("facts") or {}
        doc_type = parsed["document_type"]
        if doc_type == "bank_statement":
            month = facts.get("statement_month")
            months = list(updated.get("statement_months") or [])
            if month and month not in months:
                months.append(month)
            updated["statement_months"] = months
            if month and facts.get("declared_pages"):
                pages = dict(updated.get("statement_pages") or {})
                seen = set((pages.get(month) or {}).get("seen_pages") or [])
                seen.update(facts.get("seen_page_markers") or [])
                pages[month] = {
                    "declared_pages": facts.get("declared_pages"),
                    "seen_pages": sorted(seen),
                }
                updated["statement_pages"] = pages
        elif doc_type == "financial_statements":
            fill(updated, {"preparer_name": facts.get("preparer")})
        return updated

    def blockers(self, row) -> list:
        from services.common.clock import now

        extracted = row.get("extracted") or {}
        docs = extracted.get("docs") or {}
        blockers = []
        months = set(extracted.get("statement_months") or [])
        for month in REQUIRED_MONTHS:
            if month not in months:
                blockers.append(f"MISSING_DOC:bank_statement_{month}")
        for month, info in (extracted.get("statement_pages") or {}).items():
            declared = info.get("declared_pages") or 0
            seen = info.get("seen_pages") or []
            if declared and len(seen) < declared:
                blockers.append(f"MISSING_PAGE:bank_statement_{month}")
        if not docs.get("financial_statements"):
            blockers.append("MISSING_DOC:financial_statements")
        has_tax = docs.get("tax_return") or (
            docs.get("tax_extension") and _young(extracted.get("formation_date") or "", now())
        )
        if not has_tax and not docs.get("tax_return"):
            blockers.append("MISSING_DOC:tax_return")
        return blockers


STAGE = Credit()
