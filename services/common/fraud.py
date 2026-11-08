from services.bootstrap import ensure_fraud_detected
from services.common.pocketbase import escape_filter


def fraud_for(pb, folder: str):
    ensure_fraud_detected(pb)
    rows = pb.list_records("fraud_detected", f"folder='{escape_filter(folder)}'")
    return rows[0] if rows else None
