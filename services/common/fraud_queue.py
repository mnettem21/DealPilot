"""Pipeline side of a detection result.

The Docker agent only reports a level. This module decides what the fraud team sees
and when a deal is allowed to leave Committed.
"""

from services.common.pocketbase import PocketBaseError, escape_filter


def queue_for(payload, at):
    """Return (table, row) for an agent result. None when there is nothing to queue."""
    level = payload.get("level")
    if level not in {"warning", "critical"}:
        return None
    row = {
        "folder": payload["folder"],
        "at": at,
        "stage": payload.get("stage") or "",
        "reason": payload.get("reason") or "",
        "status": "open",
    }
    if level == "warning":
        return "fraud_warnings", row
    row["label"] = "fraud critical"
    return "fraud_critical", row


def committed_block(status):
    """What Committed rules should do with a fraud_critical row.

    open: the deal sits at Committed until the fraud team decides.
    rejected: the fraud team already said no.
    None: no open critical row, including a warning-only deal, so it may move on.
    """
    if status == "open":
        return "wait"
    if status == "rejected":
        return "decline"
    return None


def critical_status(pb, folder):
    expr = f"folder='{escape_filter(folder)}'"
    try:
        rows = pb.list_records("fraud_critical", expr)
    except PocketBaseError as exc:
        if exc.status == 404:
            return None
        raise
    if not rows:
        return None
    return rows[0].get("status") or "open"
