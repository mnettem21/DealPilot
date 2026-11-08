import re

from .base import Stage, fill


def _norm(value) -> str:
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def _differ(left, right) -> bool:
    a, b = _norm(left), _norm(right)
    return bool(a and b and a != b)


class Intake(Stage):
    name = "intake"
    next_name = "credit"
    sla_hours = 8
    doc_types = frozenset(
        {"loan_application", "guarantor_id", "certificate_of_formation", "ein_letter"}
    )

    def assignee(self, extracted, contacts, now) -> str:
        return "olivia.chen"

    def absorb(self, extracted, parsed) -> dict:
        updated = super().absorb(extracted, parsed)
        facts = parsed.get("facts") or {}
        doc_type = parsed["document_type"]
        if doc_type == "loan_application":
            fill(
                updated,
                {
                    "legal_name": facts.get("legal_name"),
                    "ein": facts.get("ein"),
                    "amount_requested": facts.get("amount_requested"),
                    "purpose_category": facts.get("purpose_category"),
                    "product": facts.get("product"),
                    "term_months": facts.get("term_months"),
                    "guarantor_name": facts.get("guarantor_name"),
                    "guarantor_dob": facts.get("guarantor_dob"),
                    "annual_revenue_stated": facts.get("annual_revenue_stated"),
                    "needed_by": facts.get("needed_by"),
                    "formation_date": facts.get("formation_date"),
                    "business_address": facts.get("business_address"),
                },
            )
        elif doc_type == "certificate_of_formation":
            fill(
                updated,
                {
                    "formation_legal_name": facts.get("formation_legal_name"),
                    "formation_date": facts.get("formation_date_support") or updated.get("formation_date"),
                    "business_address": facts.get("formation_address"),
                    "registered_agent": facts.get("registered_agent"),
                    "registered_agent_address": facts.get("registered_agent_address"),
                },
            )
        elif doc_type == "ein_letter":
            fill(
                updated,
                {
                    "ein_legal_name": facts.get("ein_legal_name"),
                    "ein_support": facts.get("ein_support"),
                },
            )
        elif doc_type == "guarantor_id":
            fill(updated, {"id_name": facts.get("id_name")})
        return updated

    def blockers(self, row) -> list:
        extracted = row.get("extracted") or {}
        docs = extracted.get("docs") or {}
        missing = {
            "loan_application": "application",
            "guarantor_id": "guarantor_id",
            "certificate_of_formation": "certificate_of_formation",
            "ein_letter": "ein_letter",
        }
        blockers = [f"MISSING_DOC:{label}" for key, label in missing.items() if not docs.get(key)]
        if _differ(extracted.get("legal_name"), extracted.get("formation_legal_name")):
            blockers.append("MISMATCH:legal_name")
        if _differ(extracted.get("legal_name"), extracted.get("ein_legal_name")):
            blockers.append("MISMATCH:legal_name")
        if _differ(extracted.get("ein"), extracted.get("ein_support")):
            blockers.append("MISMATCH:ein")
        if _differ(extracted.get("guarantor_name"), extracted.get("id_name")):
            blockers.append("MISMATCH:guarantor_name")
        return blockers


STAGE = Intake()
