import re
from datetime import datetime, timedelta

from .config import STAGES


DOCUMENT_REQUIREMENTS = {
    "Intake": {"loan_application", "guarantor_id", "certificate_of_formation", "ein_letter"},
    "Credit": {"financial_statements"},
}

HUMAN_GATE = {
    "Credit": "credit_decision",
    "Committee": "committee_approval",
    "Committed": "commitment_signoff",
}

SLA_HOURS = {
    "Intake": 8,
    "Credit": 48,
    "Committee": 48,
    "Product": 16,
    "Committed": 48,
    "Closed": 24,
}

ALLOWED_ANSWERS = {
    "credit_decision": {"approve", "decline", "request_info"},
    "committee_approval": {"approve", "decline", "request_info"},
    "commitment_signoff": {"approve", "decline", "request_info"},
    "funding_release": {"approve", "decline", "request_info"},
    "fraud_hold": {"approve", "decline", "request_info"},
    "fraud_release": {"approve", "decline", "request_info"},
    "info_request": {"received", "cancel"},
}

ROLE_FOR_REQUEST = {
    "credit_decision": "credit_officer",
    "committee_approval": "committee",
    "commitment_signoff": "relationship_manager",
    "funding_release": "funding_officer",
    "fraud_hold": "fraud_ops",
    "fraud_release": "fraud_ops",
    "info_request": "borrower",
}


def deal_id(folder: str) -> str:
    return "deal_" + re.sub(r"[^a-z0-9]+", "_", folder.lower()).strip("_")


def parse_time(value: str) -> datetime:
    return datetime.fromisoformat(value)


def parse_delta(value: str) -> timedelta:
    match = re.fullmatch(r"(\d+(?:\.\d+)?)([mhd])", value.strip())
    if not match:
        raise ValueError("Use a duration such as 30m, 4h, or 1d")
    number = float(match.group(1))
    unit = match.group(2)
    if unit == "m":
        return timedelta(minutes=number)
    if unit == "h":
        return timedelta(hours=number)
    return timedelta(days=number)


def next_stage(stage: str):
    index = STAGES.index(stage)
    return STAGES[index + 1] if index + 1 < len(STAGES) else None


def is_blocking(issue: str) -> bool:
    return issue.startswith(("MISSING_DOC:", "MISSING_PAGE:", "MISMATCH:", "OLD_TRICK:"))

