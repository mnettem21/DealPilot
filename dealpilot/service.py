import csv
import hashlib
import json
import re
from datetime import datetime
from pathlib import Path

from . import db
from .config import (
    ARRIVALS_PATH,
    ASSIGNMENTS_PATH,
    CONTACTS_PATH,
    HISTORY_DEALS_PATH,
    OUTPUT_DIR,
    SHARED_FOLDER,
    SIM_START,
    STAGES,
)
from .domain import (
    ALLOWED_ANSWERS,
    ROLE_FOR_REQUEST,
    SLA_HOURS,
    deal_id,
    is_blocking,
    next_stage,
    parse_delta,
    parse_time,
)
from .extractor import process_document


MONTHS = {f"2026-{month:02d}" for month in range(5, 11)}
INTAKE_TYPES = {"loan_application", "guarantor_id", "certificate_of_formation", "ein_letter"}
ISSUE_DOC_LABEL = {"loan_application": "application"}


def load_json(path: Path):
    return json.loads(path.read_text())


def load_arrivals():
    rows = [json.loads(line) for line in ARRIVALS_PATH.read_text().splitlines() if line.strip()]
    return sorted(rows, key=lambda item: item["arrive_at"])


def load_history():
    with HISTORY_DEALS_PATH.open(newline="") as stream:
        return list(csv.DictReader(stream))


def normalize(value):
    return re.sub(r"[^a-z0-9]", "", str(value or "").lower())


def initialize():
    db.initialize()
    with db.transaction() as conn:
        db.event(conn, "ApplicationInitialized", payload={"version": "0.1.0"})


def reset(clear_portal=False):
    # Reset is also the first action in the demo UI, so it must work on a
    # completely fresh checkout where the SQLite file has not been created yet.
    db.initialize()
    with db.transaction() as conn:
        # The simulator owns only deal_* records by default. Borrower-portal applications
        # use portal_* ids and can survive a judge resetting the sample clock unless clear_portal is True.
        if clear_portal:
            sample_ids = [row["id"] for row in conn.execute("SELECT id FROM deals")]
            conn.execute("DELETE FROM borrower_applications")
        else:
            sample_ids = [row["id"] for row in conn.execute("SELECT id FROM deals WHERE id LIKE 'deal_%'")]
        if sample_ids:
            placeholders = ",".join("?" for _ in sample_ids)
            for table in ("alerts", "human_requests", "stage_events", "documents"):
                conn.execute(f"DELETE FROM {table} WHERE deal_id IN ({placeholders})", sample_ids)
            conn.execute(f"DELETE FROM audit_events WHERE deal_id IN ({placeholders})", sample_ids)
            conn.execute(f"DELETE FROM deals WHERE id IN ({placeholders})", sample_ids)
        db.set_state(conn, "sim_now", SIM_START)
        db.event(conn, "SimulationReset", at=SIM_START)
    return {"now": SIM_START, "processed": 0}


def sim_now(conn=None):
    own = conn is None
    conn = conn or db.connect()
    try:
        return db.get_state(conn, "sim_now", SIM_START)
    finally:
        if own:
            conn.close()


def advance_clock(duration: str):
    with db.transaction() as conn:
        current = parse_time(db.get_state(conn, "sim_now", SIM_START))
        updated = current + parse_delta(duration)
        updated_text = updated.isoformat(timespec="seconds")
        db.set_state(conn, "sim_now", updated_text)
        db.event(conn, "ClockAdvanced", payload={"by": duration}, at=updated_text)
    processed = ingest_arrived()
    evaluate_all()
    return {"now": updated_text, "processed": processed}


def create_deal(conn, folder: str, at: str):
    identifier = deal_id(folder)
    assignments = load_json(ASSIGNMENTS_PATH).get(folder, {})
    conn.execute(
        """INSERT OR IGNORE INTO deals(
             id, folder, stage, operational_state, entered_at,
             relationship_manager, credit_officer, updated_at
           ) VALUES(?, ?, 'Intake', 'PROCESSING', ?, ?, ?, ?)""",
        (
            identifier,
            folder,
            at,
            assignments.get("relationship_manager"),
            assignments.get("credit_officer"),
            at,
        ),
    )
    if conn.execute("SELECT changes()").fetchone()[0]:
        conn.execute(
            "INSERT INTO stage_events(deal_id, stage, entered_at) VALUES(?, 'Intake', ?)",
            (identifier, at),
        )
        db.event(conn, "DealCreated", identifier, payload={"folder": folder}, at=at)
    return identifier


def ingest_arrived():
    processed = 0
    with db.transaction() as conn:
        now = parse_time(db.get_state(conn, "sim_now", SIM_START))
        for arrival in load_arrivals():
            if parse_time(arrival["arrive_at"]) > now:
                break
            if conn.execute("SELECT 1 FROM documents WHERE path=?", (arrival["path"],)).fetchone():
                continue
            source = SHARED_FOLDER / arrival["path"]
            folder = arrival["path"].split("/", 1)[0]
            identifier = create_deal(conn, folder, arrival["arrive_at"])
            try:
                result = process_document(source)
            except Exception as exc:
                result = {
                    "checksum": hashlib.sha256(source.read_bytes()).hexdigest(),
                    "document_type": "unknown",
                    "status": "MANUAL_REVIEW",
                    "page_count": None,
                    "producer": "",
                    "text": "",
                    "facts": {},
                    "error": str(exc),
                }
            doc_id = "doc_" + result["checksum"][:16]
            conn.execute(
                """INSERT INTO documents(
                     id, deal_id, path, filename, checksum, arrived_at, document_type,
                     status, page_count, producer, extracted_text, facts_json, created_at
                   ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    doc_id,
                    identifier,
                    arrival["path"],
                    source.name,
                    result["checksum"],
                    arrival["arrive_at"],
                    result["document_type"],
                    result["status"],
                    result["page_count"],
                    result["producer"],
                    result["text"],
                    json.dumps(result["facts"]),
                    now.isoformat(timespec="seconds"),
                ),
            )
            conn.execute(
                "UPDATE deals SET evidence_version=evidence_version+1, updated_at=? WHERE id=?",
                (now.isoformat(timespec="seconds"), identifier),
            )
            invalidate_approvals(conn, identifier, now.isoformat(timespec="seconds"))
            db.event(
                conn,
                "DocumentProcessed" if result["status"] == "PROCESSED" else "DocumentNeedsReview",
                identifier,
                payload={
                    "document_id": doc_id,
                    "path": arrival["path"],
                    "type": result["document_type"],
                    "error": result.get("error"),
                },
                at=arrival["arrive_at"],
            )
            processed += 1
        deal_ids = [row["id"] for row in conn.execute("SELECT id FROM deals")]
        for identifier in deal_ids:
            recompute_deal(conn, identifier)
        recompute_fraud(conn)
    return processed


def process_portal_document(identifier: str, source: Path, original_filename: str, actor: str):
    result = process_document(source)
    now = sim_now()
    doc_id = "doc_" + hashlib.sha256(f"{identifier}:{result['checksum']}:{source.name}".encode()).hexdigest()[:16]
    with db.transaction() as conn:
        if not conn.execute("SELECT 1 FROM deals WHERE id=?", (identifier,)).fetchone():
            raise ValueError("Application deal not found")
        conn.execute(
            """INSERT INTO documents(
                 id,deal_id,path,filename,checksum,arrived_at,document_type,status,
                 page_count,producer,extracted_text,facts_json,created_at
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                doc_id, identifier, str(source), original_filename, result["checksum"], now,
                result["document_type"], result["status"], result["page_count"], result["producer"],
                result["text"], json.dumps(result["facts"]), now,
            ),
        )
        conn.execute(
            "UPDATE deals SET evidence_version=evidence_version+1,updated_at=? WHERE id=?",
            (now, identifier),
        )
        invalidate_approvals(conn, identifier, now)
        recompute_deal(conn, identifier)
        recompute_fraud(conn)
        db.event(
            conn, "BorrowerDocumentUploaded", identifier, actor=actor,
            payload={"document_id": doc_id, "filename": original_filename, "type": result["document_type"]}, at=now,
        )
    deal = get_deal(identifier)
    return next(document for document in deal["documents"] if document["id"] == doc_id)


def invalidate_approvals(conn, identifier: str, at: str):
    rows = conn.execute(
        """SELECT id, type FROM human_requests
           WHERE deal_id=? AND status='answered' AND invalidated_at IS NULL
           AND type NOT IN ('fraud_hold', 'fraud_release')""",
        (identifier,),
    ).fetchall()
    for row in rows:
        conn.execute("UPDATE human_requests SET status='invalidated', invalidated_at=? WHERE id=?", (at, row["id"]))
        db.event(conn, "ApprovalInvalidated", identifier, payload={"request_id": row["id"], "type": row["type"]}, at=at)


def recompute_deal(conn, identifier: str):
    deal = conn.execute("SELECT * FROM deals WHERE id=?", (identifier,)).fetchone()
    docs = conn.execute("SELECT * FROM documents WHERE deal_id=? ORDER BY arrived_at", (identifier,)).fetchall()
    by_type = {}
    extracted = {}
    producers = set()
    bank_statements = []
    for row in docs:
        by_type.setdefault(row["document_type"], []).append(row)
        facts = json.loads(row["facts_json"] or "{}")
        if row["document_type"] == "loan_application":
            extracted.update(facts)
        else:
            for key, value in facts.items():
                extracted.setdefault(key, value)
        if row["producer"]:
            producers.add(row["producer"])
        if row["document_type"] == "bank_statement":
            bank_statements.append((row, facts))

    issues = []
    for required in sorted(INTAKE_TYPES):
        if required not in by_type:
            issues.append(f"MISSING_DOC:{ISSUE_DOC_LABEL.get(required, required)}")

    statement_months = {facts.get("statement_month") for _, facts in bank_statements}
    for month in sorted(MONTHS - statement_months):
        issues.append(f"MISSING_DOC:bank_statement_{month}")
    if "financial_statements" not in by_type:
        issues.append("MISSING_DOC:financial_statements")

    formation = extracted.get("formation_date")
    young_business = False
    if formation:
        try:
            young_business = (parse_time(deal["entered_at"]).date() - datetime.fromisoformat(formation).date()).days < 730
        except ValueError:
            pass
    if "tax_return" not in by_type and not (young_business and "tax_extension" in by_type):
        issues.append("MISSING_DOC:tax_return")

    if extracted.get("product") == "Equipment Loan" and "equipment_quote" not in by_type:
        issues.append("MISSING_DOC:equipment_quote")

    for row, facts in bank_statements:
        declared = facts.get("declared_pages")
        markers = facts.get("seen_page_markers", [])
        if declared and (row["page_count"] < declared or len(markers) < declared):
            month = facts.get("statement_month") or "unknown"
            issues.append(f"MISSING_PAGE:bank_statement_{month}")

    mismatch(issues, "legal_name", extracted.get("legal_name"), extracted.get("formation_legal_name"))
    mismatch(issues, "ein", extracted.get("ein"), extracted.get("ein_support"))
    mismatch(issues, "guarantor_name", extracted.get("guarantor_name"), extracted.get("id_name"))
    mismatch(issues, "guarantor_dob", extracted.get("guarantor_dob"), extracted.get("id_dob"))
    mismatch(issues, "guarantor_address", extracted.get("guarantor_address"), extracted.get("id_address"))

    application_ein = extracted.get("ein")
    if application_ein:
        history_matches = [row for row in load_history() if row["ein"] == application_ein and normalize(row["legal_name"]) != normalize(extracted.get("legal_name"))]
        if history_matches:
            issues.append("OLD_TRICK:DUP_EIN")

    address = (extracted.get("business_address") or "").lower()
    if any(token in address for token in ("pmb", "private mailbox", "ups store")):
        issues.append("OLD_TRICK:SHELL_ADDRESS")

    deposits = [facts.get("total_deposits") for _, facts in bank_statements if facts.get("total_deposits")]
    stated = extracted.get("annual_revenue_stated")
    if len(deposits) >= 3 and stated:
        annualized = sum(deposits) / len(deposits) * 12
        extracted["annualized_deposits"] = round(annualized, 2)
        if stated > annualized * 1.5:
            issues.append("OLD_TRICK:REV_INFLATION")

    sorted_statements = sorted(bank_statements, key=lambda item: item[1].get("statement_month", ""))
    for (_, previous), (_, current) in zip(sorted_statements, sorted_statements[1:]):
        close = previous.get("closing_balance")
        opening = current.get("opening_balance")
        if close is not None and opening is not None and abs(close - opening) > 0.01:
            issues.append("OLD_TRICK:STMT_TAMPER")
            break

    if extracted.get("quote_total") and extracted.get("amount_requested"):
        if abs(extracted["quote_total"] - extracted["amount_requested"]) > 1:
            issues.append("MISMATCH:amount_requested")

    extracted["pdf_producers"] = sorted(producers)
    needed = extracted.get("needed_by")
    needed_by = parse_needed_by(needed)
    conn.execute(
        "UPDATE deals SET extracted_json=?, issues_json=?, needed_by=?, updated_at=? WHERE id=?",
        (json.dumps(extracted), json.dumps(sorted(set(issues))), needed_by, sim_now(conn), identifier),
    )


def mismatch(issues, field, first, second):
    if first and second and normalize(first) != normalize(second):
        issues.append(f"MISMATCH:{field}")


def parse_needed_by(value):
    if not value or "no specific" in value.lower():
        return None
    match = re.search(r"(2026-\d{2}-\d{2}).*?(\d{1,2}):?(\d{2})?\s*(AM|PM)?", value, re.I)
    if not match:
        return None
    hour = int(match.group(2))
    minute = int(match.group(3) or 0)
    if (match.group(4) or "").upper() == "PM" and hour < 12:
        hour += 12
    return f"{match.group(1)}T{hour:02d}:{minute:02d}:00-06:00"


def feature_map(conn, identifier):
    deal = db.decode_row(conn.execute("SELECT * FROM deals WHERE id=?", (identifier,)).fetchone())
    extracted = deal["extracted"]
    features = {
        "registered_agent": extracted.get("registered_agent"),
        "registered_agent_address": extracted.get("registered_agent_address"),
        "preparer": extracted.get("preparer"),
        "preparer_phone": extracted.get("preparer_phone"),
        "equipment_vendor": extracted.get("equipment_vendor"),
        "vendor_remit_acct_last4": extracted.get("vendor_remit_acct_last4"),
        "vendor_routing": extracted.get("vendor_routing"),
        "bank_account": extracted.get("bank_account"),
    }
    docs = conn.execute("SELECT producer, facts_json FROM documents WHERE deal_id=?", (identifier,)).fetchall()
    producers = sorted({row["producer"] for row in docs if row["producer"]})
    transfer_accounts = set()
    for row in docs:
        facts = json.loads(row["facts_json"] or "{}")
        transfer_accounts.update(facts.get("transfer_accounts", []))
    features["pdf_producer"] = producers[0] if len(producers) == 1 else None
    features["transfer_account"] = sorted(transfer_accounts)[0] if len(transfer_accounts) == 1 else None
    dl = extracted.get("guarantor_dl_number")
    features["dl_prefix"] = dl[:6] if dl and len(dl) >= 6 else None
    amount = extracted.get("amount_requested")
    features["delegated_threshold_cluster"] = "under_250k" if amount and 240000 <= amount < 250000 else None
    return {key: normalize(value) for key, value in features.items() if value}


FEATURE_WEIGHTS = {
    # High-Entropy Financial Rails & Extraction Channels (Direct theft vectors)
    "vendor_remit_acct_last4": 4.5,
    "transfer_account": 4.0,
    "vendor_routing": 3.0,
    "bank_account": 3.0,
    # Synthetic Identity and Provenance Signatures
    "dl_prefix": 3.5,
    "pdf_producer": 2.5,
    # Behavioral Structuring
    "delegated_threshold_cluster": 2.5,
    # Common Service Providers (Low entropy, Texas businesses legitimately share these)
    "preparer_phone": 2.0,
    "equipment_vendor": 1.5,
    "preparer": 1.5,
    "registered_agent_address": 1.0,
    "registered_agent": 1.0,
}

FEATURE_FAMILIES = {
    "vendor_remit_acct_last4": "financial",
    "transfer_account": "financial",
    "vendor_routing": "financial",
    "bank_account": "financial",
    "dl_prefix": "identity",
    "pdf_producer": "provenance",
    "delegated_threshold_cluster": "structuring",
    "preparer_phone": "service_provider",
    "preparer": "service_provider",
    "equipment_vendor": "service_provider",
    "registered_agent_address": "service_provider",
    "registered_agent": "service_provider",
}


def recompute_fraud(conn):
    identifiers = [row["id"] for row in conn.execute("SELECT id FROM deals")]
    maps = {identifier: feature_map(conn, identifier) for identifier in identifiers}
    history = load_history()
    for identifier in identifiers:
        links = []
        evidence = set()
        for other in identifiers:
            if other == identifier:
                continue
            shared = sorted(key for key, value in maps[identifier].items() if value and maps[other].get(key) == value)
            weight = sum(FEATURE_WEIGHTS.get(k, 1.0) for k in shared)
            families = {FEATURE_FAMILIES.get(k) for k in shared if FEATURE_FAMILIES.get(k)}
            # Weighted link criterion: strong financial/identity anchors create an edge
            # with fewer features, while service provider overlaps alone do not cross threshold.
            if (weight >= 7.0 and len(families) >= 2) or len(shared) >= 6:
                folder = conn.execute("SELECT folder FROM deals WHERE id=?", (other,)).fetchone()["folder"]
                links.append({"deal": folder, "shared": shared, "weight": weight})
                evidence.update(shared)

        for row in history:
            historical = {
                "registered_agent": normalize(row.get("registered_agent")),
                "preparer": normalize(row.get("preparer_name")),
                "preparer_phone": normalize(row.get("preparer_phone")),
                "equipment_vendor": normalize(row.get("equipment_vendor")),
                "vendor_remit_acct_last4": normalize(row.get("vendor_remit_acct_last4")),
                "bank_account": normalize(row.get("bank_name")),
                "pdf_producer": normalize(row.get("pdf_producer")),
                "dl_prefix": normalize(row.get("guarantor_dl_number", "")[:6]),
            }
            shared = sorted(key for key, value in maps[identifier].items() if value and historical.get(key) == value)
            weight = sum(FEATURE_WEIGHTS.get(k, 1.0) for k in shared)
            families = {FEATURE_FAMILIES.get(k) for k in shared if FEATURE_FAMILIES.get(k)}
            if (weight >= 10.0 and len(families) >= 3) or len(shared) >= 5:
                links.append({"deal": row["deal_id"], "shared": shared, "historical": True, "weight": weight})
                evidence.update(shared)

        total_weight = sum(FEATURE_WEIGHTS.get(k, 1.0) for k in evidence)
        evidence_families = {FEATURE_FAMILIES.get(k) for k in evidence if FEATURE_FAMILIES.get(k)}
        flagged = bool(links and total_weight >= 7.0 and len(evidence_families) >= 2)
        amount = db.decode_row(conn.execute("SELECT * FROM deals WHERE id=?", (identifier,)).fetchone())["extracted"].get("amount_requested")
        reasons = [humanize_feature(value) for value in sorted(evidence)]
        if amount and 240000 <= amount < 250000 and links:
            reasons.append("Loan amount clusters immediately below the $250,000 committee threshold")

        deal_row = conn.execute("SELECT stage, fraud_json FROM deals WHERE id=?", (identifier,)).fetchone()
        existing_fraud = json.loads(deal_row["fraud_json"] or "{}") if deal_row else {}
        current_stage = deal_row["stage"] if deal_row else "Intake"

        if existing_fraud.get("flagged"):
            flagged_at = existing_fraud.get("flagged_at") or sim_now(conn)
            stage_at_flag = existing_fraud.get("stage_at_flag") or current_stage
        elif flagged:
            flagged_at = sim_now(conn)
            stage_at_flag = current_stage
        else:
            flagged_at = None
            stage_at_flag = None

        fraud = {
            "flagged": flagged,
            "flagged_at": flagged_at,
            "stage_at_flag": stage_at_flag,
            "category": "organized_fraud_ring" if flagged else None,
            "confidence": min(0.99, round(0.50 + total_weight * 0.02, 2)) if flagged else 0,
            "held": active_hold(conn, identifier),
            "reason": "; ".join(reasons) if flagged else None,
            "evidence": reasons,
            "counter_evidence": ["Distinct EIN", "Distinct legal name"] if flagged else [],
            "linked_deals": [link["deal"] for link in links],
            "links": links,
        }
        conn.execute("UPDATE deals SET fraud_json=? WHERE id=?", (json.dumps(fraud), identifier))
        if flagged:
            remember_fraud_pattern(conn, evidence, len(links) + 1)
            db.event(conn, "FraudFindingUpdated", identifier, payload=fraud, at=sim_now(conn))


def remember_fraud_pattern(conn, evidence, occurrences):
    features = sorted(evidence)
    if not features:
        return
    signature = hashlib.sha256("|".join(features).encode()).hexdigest()
    now = sim_now(conn)
    name = " + ".join(humanize_feature(feature) for feature in features[:3])
    conn.execute(
        """INSERT INTO fraud_patterns(signature,name,features_json,occurrences,first_seen,last_seen,status)
           VALUES(?,?,?,?,?,?,'learned')
           ON CONFLICT(signature) DO UPDATE SET
             occurrences=MAX(fraud_patterns.occurrences, excluded.occurrences),
             last_seen=excluded.last_seen""",
        (signature, name, json.dumps(features), occurrences, now, now),
    )


def humanize_feature(value):
    return {
        "registered_agent": "Shared registered agent",
        "registered_agent_address": "Shared registered-agent address",
        "preparer": "Shared financial preparer",
        "preparer_phone": "Shared preparer phone",
        "equipment_vendor": "Shared equipment vendor",
        "vendor_remit_acct_last4": "Shared vendor remit account",
        "vendor_routing": "Shared vendor routing number",
        "bank_account": "Shared operating bank",
        "pdf_producer": "Shared PDF producer",
        "transfer_account": "Shared transfer destination",
        "dl_prefix": "Near-sequential driver-license range",
        "delegated_threshold_cluster": "Amounts clustered below delegated threshold",
    }.get(value, value.replace("_", " ").title())


def active_hold(conn, identifier):
    row = conn.execute(
        """SELECT type, answer FROM human_requests
           WHERE deal_id=? AND type IN ('fraud_hold','fraud_release') AND status='answered'
           ORDER BY id DESC LIMIT 1""",
        (identifier,),
    ).fetchone()
    if not row:
        return False
    return row["type"] == "fraud_hold" and row["answer"] == "approve"


def request_exists(conn, identifier, request_type):
    return conn.execute(
        """SELECT 1 FROM human_requests
           WHERE deal_id=? AND type=? AND status IN ('waiting','answered')
           AND invalidated_at IS NULL LIMIT 1""",
        (identifier, request_type),
    ).fetchone() is not None


def create_request(conn, identifier, request_type, recipient, message):
    deal = conn.execute("SELECT evidence_version FROM deals WHERE id=?", (identifier,)).fetchone()
    now = sim_now(conn)
    cursor = conn.execute(
        """INSERT INTO human_requests(
             deal_id, type, recipient, requested_at, message, evidence_version
           ) VALUES(?,?,?,?,?,?)""",
        (identifier, request_type, recipient, now, message, deal["evidence_version"]),
    )
    db.event(conn, "HumanRequestCreated", identifier, payload={"id": cursor.lastrowid, "type": request_type, "to": recipient}, at=now)
    return cursor.lastrowid


def recipient_for(conn, deal, request_type):
    if request_type == "credit_decision":
        return active_credit_officer(deal["credit_officer"], sim_now(conn))
    if request_type == "committee_approval":
        amount = json.loads(deal["extracted_json"] or "{}").get("amount_requested") or 0
        return "priscilla.nguyen" if amount < 250000 else "harold.benson"
    if request_type == "commitment_signoff":
        return deal["relationship_manager"] or "borrower"
    if request_type == "funding_release":
        return "tasha.reed"
    if request_type in {"fraud_hold", "fraud_release"}:
        return "victor.hale"
    return "borrower"


def active_credit_officer(person_id, now_text):
    contacts = load_json(CONTACTS_PATH)["people"]
    person = next((person for person in contacts if person["id"] == person_id), None)
    if not person:
        return person_id or "marcus.lee"
    note = person.get("notes", "")
    match = re.search(r"OUT OF OFFICE ([0-9:T -]+) through ([0-9:T -]+)", note)
    if match:
        now = parse_time(now_text).replace(tzinfo=None)
        start = datetime.fromisoformat(match.group(1).strip())
        end = datetime.fromisoformat(match.group(2).strip())
        if start <= now <= end:
            return person.get("backup") or person_id
    return person_id


def stage_blockers(deal, stage):
    issues = json.loads(deal["issues_json"] or "[]")
    if stage == "Intake":
        allowed = {"application", "guarantor_id", "certificate_of_formation", "ein_letter"}
        return [issue for issue in issues if issue.startswith("MISMATCH:") or issue.split(":", 1)[-1] in allowed]
    if stage == "Credit":
        return [issue for issue in issues if is_blocking(issue)]
    if stage == "Product":
        return [issue for issue in issues if issue.startswith(("MISSING_DOC:equipment_quote", "MISMATCH:amount_requested"))]
    return []


def evaluate_all():
    with db.transaction() as conn:
        for row in conn.execute("SELECT id FROM deals ORDER BY entered_at"):
            evaluate_deal(conn, row["id"])
        run_sla_checks(conn)


def evaluate_deal(conn, identifier):
    deal = conn.execute("SELECT * FROM deals WHERE id=?", (identifier,)).fetchone()
    fraud = json.loads(deal["fraud_json"] or "{}")
    if active_hold(conn, identifier):
        conn.execute("UPDATE deals SET operational_state='HELD_FRAUD' WHERE id=?", (identifier,))
        return
    if fraud.get("flagged") and not request_exists(conn, identifier, "fraud_hold"):
        recipient = recipient_for(conn, deal, "fraud_hold")
        create_request(conn, identifier, "fraud_hold", recipient, fraud_summary(deal, fraud))
        conn.execute("UPDATE deals SET operational_state='WAITING_HUMAN' WHERE id=?", (identifier,))
        return
    if fraud.get("flagged") and conn.execute(
        "SELECT 1 FROM human_requests WHERE deal_id=? AND type='fraud_hold' AND status='waiting' LIMIT 1",
        (identifier,),
    ).fetchone():
        # A portfolio reevaluation must never move a deal around an unresolved
        # fraud gate merely because the request already exists.
        conn.execute("UPDATE deals SET operational_state='WAITING_HUMAN' WHERE id=?", (identifier,))
        return

    stage = deal["stage"]
    blockers = stage_blockers(deal, stage)
    if blockers:
        conn.execute("UPDATE deals SET operational_state='WAITING_DOCUMENTS' WHERE id=?", (identifier,))
        return

    if stage == "Intake":
        transition(conn, identifier, "Credit", "advanced", "Required intake documents are present and consistent")
        evaluate_deal(conn, identifier)
        return

    if stage == "Credit":
        ensure_request(conn, identifier, "credit_decision")
        conn.execute("UPDATE deals SET operational_state='WAITING_HUMAN' WHERE id=?", (identifier,))
        return

    if stage == "Committee":
        ensure_request(conn, identifier, "committee_approval")
        conn.execute("UPDATE deals SET operational_state='WAITING_HUMAN' WHERE id=?", (identifier,))
        return

    if stage == "Product":
        transition(conn, identifier, "Committed", "advanced", "Product, term, pricing and vendor requirements verified")
        evaluate_deal(conn, identifier)
        return

    if stage == "Committed":
        if approved_request(conn, identifier, "commitment_signoff"):
            if not request_exists(conn, identifier, "funding_release"):
                ensure_request(conn, identifier, "funding_release")
            conn.execute("UPDATE deals SET operational_state='READY_TO_CLOSE' WHERE id=?", (identifier,))
        else:
            ensure_request(conn, identifier, "commitment_signoff")
            conn.execute("UPDATE deals SET operational_state='WAITING_HUMAN' WHERE id=?", (identifier,))


def ensure_request(conn, identifier, request_type):
    if request_exists(conn, identifier, request_type):
        return
    deal = conn.execute("SELECT * FROM deals WHERE id=?", (identifier,)).fetchone()
    recipient = recipient_for(conn, deal, request_type)
    extracted = json.loads(deal["extracted_json"] or "{}")
    message = (
        f"{deal['folder']} requests ${extracted.get('amount_requested') or 0:,.0f} "
        f"for {extracted.get('purpose') or 'the stated business purpose'}. "
        f"Review the evidence-backed summary and choose an action."
    )
    create_request(conn, identifier, request_type, recipient, message)


def approved_request(conn, identifier, request_type):
    return conn.execute(
        """SELECT 1 FROM human_requests
           WHERE deal_id=? AND type=? AND status='answered' AND answer='approve'
           AND invalidated_at IS NULL LIMIT 1""",
        (identifier, request_type),
    ).fetchone() is not None


def transition(conn, identifier, destination, decision, why):
    deal = conn.execute("SELECT stage, entered_at FROM deals WHERE id=?", (identifier,)).fetchone()
    now = sim_now(conn)
    conn.execute(
        """UPDATE stage_events SET exited_at=?, decision=?, why=?
           WHERE id=(SELECT id FROM stage_events WHERE deal_id=? AND exited_at IS NULL ORDER BY id DESC LIMIT 1)""",
        (now, decision, why, identifier),
    )
    conn.execute(
        "UPDATE deals SET stage=?, operational_state='PROCESSING', entered_at=?, updated_at=? WHERE id=?",
        (destination, now, now, identifier),
    )
    conn.execute("INSERT INTO stage_events(deal_id, stage, entered_at) VALUES(?,?,?)", (identifier, destination, now))
    db.event(conn, "StageEntered", identifier, payload={"from": deal["stage"], "to": destination, "why": why}, at=now)


def answer_request(request_id: int, answer: str, actor: str, role: str):
    with db.transaction() as conn:
        request = conn.execute("SELECT * FROM human_requests WHERE id=?", (request_id,)).fetchone()
        if not request:
            raise ValueError("Human request not found")
        if request["status"] != "waiting":
            raise ValueError("This request is no longer waiting")
        if answer not in ALLOWED_ANSWERS.get(request["type"], set()):
            raise ValueError("Answer is not valid for this request")
        expected_role = ROLE_FOR_REQUEST.get(request["type"])
        if role not in {expected_role, "admin"}:
            raise PermissionError(f"This action requires the {expected_role} role")
        now = sim_now(conn)
        conn.execute(
            "UPDATE human_requests SET status='answered', answer=?, answered_at=? WHERE id=?",
            (answer, now, request_id),
        )
        db.event(conn, "HumanRequestAnswered", request["deal_id"], actor=actor, payload={"request_id": request_id, "type": request["type"], "answer": answer}, at=now)

        if request["type"] == "fraud_hold":
            if answer == "approve":
                conn.execute("UPDATE deals SET operational_state='HELD_FRAUD' WHERE id=?", (request["deal_id"],))
            elif answer == "decline":
                conn.execute("UPDATE deals SET operational_state='PROCESSING' WHERE id=?", (request["deal_id"],))
        elif request["type"] == "fraud_release" and answer == "approve":
            conn.execute("UPDATE deals SET operational_state='PROCESSING' WHERE id=?", (request["deal_id"],))
        elif answer == "decline":
            conn.execute("UPDATE deals SET operational_state='DECLINED' WHERE id=?", (request["deal_id"],))
            close_stage(conn, request["deal_id"], "declined", f"{request['type']} declined")
        elif answer == "request_info":
            conn.execute("UPDATE deals SET operational_state='WAITING_DOCUMENTS' WHERE id=?", (request["deal_id"],))
            create_request(conn, request["deal_id"], "info_request", "borrower", "Additional information was requested by the reviewer.")
        elif answer == "approve":
            if request["type"] == "credit_decision":
                transition(conn, request["deal_id"], "Committee", "advanced", "Credit officer approved the request")
            elif request["type"] == "committee_approval":
                transition(conn, request["deal_id"], "Product", "advanced", "Committee approval received")
            elif request["type"] == "funding_release":
                transition(conn, request["deal_id"], "Closed", "funds disbursed", "Funding release approved and funds confirmed")
                conn.execute("UPDATE deals SET operational_state='CLOSED' WHERE id=?", (request["deal_id"],))
        identifier = request["deal_id"]
    evaluate_all()
    return get_deal(identifier)


def close_stage(conn, identifier, decision, why):
    now = sim_now(conn)
    conn.execute(
        """UPDATE stage_events SET exited_at=?, decision=?, why=?
           WHERE id=(SELECT id FROM stage_events WHERE deal_id=? AND exited_at IS NULL ORDER BY id DESC LIMIT 1)""",
        (now, decision, why, identifier),
    )


def fraud_summary(deal, fraud):
    return (
        f"Potential coordinated fraud involving {deal['folder']}. "
        f"Evidence: {', '.join(fraud.get('evidence', []))}. "
        f"Linked deals: {', '.join(fraud.get('linked_deals', []))}."
    )


def request_fraud_release(identifier, actor="fraud.ops"):
    with db.transaction() as conn:
        deal = conn.execute("SELECT * FROM deals WHERE id=?", (identifier,)).fetchone()
        if not deal:
            raise ValueError("Deal not found")
        if not active_hold(conn, identifier):
            raise ValueError("Deal is not on fraud hold")
        if not request_exists(conn, identifier, "fraud_release"):
            create_request(conn, identifier, "fraud_release", recipient_for(conn, deal, "fraud_release"), "Review the fraud investigation and decide whether to release the hold.")
    return get_deal(identifier)


def run_sla_checks(conn):
    now = parse_time(sim_now(conn))
    contacts = {person["id"]: person for person in load_json(CONTACTS_PATH)["people"]}
    for deal in conn.execute("SELECT * FROM deals WHERE operational_state NOT IN ('CLOSED','DECLINED','WITHDRAWN')"):
        entered = parse_time(deal["entered_at"])
        elapsed = (now - entered).total_seconds() / 3600
        sla = SLA_HOURS.get(deal["stage"], 24)
        if deal["stage"] == "Committee":
            amount = json.loads(deal["extracted_json"] or "{}").get("amount_requested") or 0
            sla = 24 if amount < 250000 else 48
        owner = owner_for_stage(deal)
        levels = []
        if elapsed >= sla:
            levels.append(("alert", owner))
            if deal["relationship_manager"] and deal["relationship_manager"] != owner:
                levels.append(("alert", deal["relationship_manager"]))
        if elapsed >= sla * 1.5:
            levels.append(("escalation", contacts.get(owner, {}).get("escalates_to") or "lauren.kim"))
        if elapsed >= sla * 2:
            levels.append(("escalation", "lauren.kim"))
        for level, recipient in levels:
            waiting = deal["operational_state"].replace("_", " ").lower()
            blockers = stage_blockers(deal, deal["stage"])
            pending = conn.execute(
                "SELECT type FROM human_requests WHERE deal_id=? AND status='waiting' ORDER BY id DESC LIMIT 1",
                (deal["id"],),
            ).fetchone()
            unblock = blockers[0] if blockers else (pending["type"] if pending else "stage-owner review")
            message = (
                f"{deal['folder']} (RM: {deal['relationship_manager']}) has been in {deal['stage']} "
                f"for {elapsed:.1f} hours. It is {waiting}. Resolve {unblock} to unblock it."
            )
            try:
                conn.execute(
                    "INSERT INTO alerts(deal_id, at, stage, level, recipient, message) VALUES(?,?,?,?,?,?)",
                    (deal["id"], sim_now(conn), deal["stage"], level, recipient, message),
                )
                db.event(conn, "AlertSent", deal["id"], payload={"level": level, "to": recipient, "message": message}, at=sim_now(conn))
            except Exception as exc:
                if "UNIQUE constraint" not in str(exc):
                    raise


def owner_for_stage(deal):
    if deal["stage"] == "Credit":
        return deal["credit_officer"] or "marcus.lee"
    if deal["stage"] == "Committee":
        amount = json.loads(deal["extracted_json"] or "{}").get("amount_requested") or 0
        return "priscilla.nguyen" if amount < 250000 else "harold.benson"
    if deal["stage"] == "Committed":
        return deal["relationship_manager"] or "lauren.kim"
    return {"Intake": "olivia.chen", "Product": "kevin.alvarez", "Closed": "tasha.reed"}.get(deal["stage"], "lauren.kim")


def get_deals():
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM deals ORDER BY COALESCE(needed_by, '9999'), entered_at").fetchall()
        return [decorate_deal(conn, db.decode_row(row), compact=True) for row in rows]


def get_deal(identifier):
    with db.connect() as conn:
        row = conn.execute("SELECT * FROM deals WHERE id=? OR folder=?", (identifier, identifier)).fetchone()
        if not row:
            return None
        return decorate_deal(conn, db.decode_row(row), compact=False)


def decorate_deal(conn, deal, compact=False):
    # Hold state is workflow state derived from the latest fraud decision. Keep
    # it live in every API response rather than waiting for a document rescan.
    deal["fraud"]["held"] = active_hold(conn, deal["id"])
    deal["stage_index"] = STAGES.index(deal["stage"])
    deal["readiness"] = readiness(deal)
    deal["current_blockers"] = stage_blockers_raw(deal)
    deal["document_count"] = conn.execute("SELECT COUNT(*) count FROM documents WHERE deal_id=?", (deal["id"],)).fetchone()["count"]
    application = conn.execute(
        "SELECT id,status,score_json,decision_json FROM borrower_applications WHERE deal_id=?",
        (deal["id"],),
    ).fetchone()
    if application:
        deal["portal_application"] = {
            "id": application["id"], "status": application["status"],
            "score": json.loads(application["score_json"] or "{}"),
            "decision": json.loads(application["decision_json"] or "{}"),
        }
    if compact:
        return deal
    deal["documents"] = [db.decode_row(row) for row in conn.execute("SELECT * FROM documents WHERE deal_id=? ORDER BY arrived_at", (deal["id"],))]
    for document in deal["documents"]:
        document.pop("extracted_text", None)
    deal["stage_log"] = [dict(row) for row in conn.execute("SELECT * FROM stage_events WHERE deal_id=? ORDER BY id", (deal["id"],))]
    deal["human_requests"] = [dict(row) for row in conn.execute("SELECT * FROM human_requests WHERE deal_id=? ORDER BY id DESC", (deal["id"],))]
    deal["alerts"] = [dict(row) for row in conn.execute("SELECT * FROM alerts WHERE deal_id=? ORDER BY id DESC", (deal["id"],))]
    deal["events"] = [db.decode_row(row) for row in conn.execute("SELECT * FROM audit_events WHERE deal_id=? ORDER BY id DESC LIMIT 100", (deal["id"],))]
    return deal


def stage_blockers_raw(deal):
    class Row(dict):
        def __getitem__(self, key):
            return super().__getitem__(key)
    raw = Row(deal)
    raw["issues_json"] = json.dumps(deal["issues"])
    return stage_blockers(raw, deal["stage"])


def readiness(deal):
    base = deal["stage_index"] / (len(STAGES) - 1) * 100
    if deal["operational_state"] == "CLOSED":
        return 100
    penalty = min(20, len(deal["issues"]) * 2)
    return max(0, round(base - penalty))


def summary():
    deals = get_deals()
    with db.connect() as conn:
        waiting = conn.execute("SELECT COUNT(*) count FROM human_requests WHERE status='waiting'").fetchone()["count"]
        alerts = conn.execute("SELECT COUNT(*) count FROM alerts").fetchone()["count"]
    return {
        "now": sim_now(),
        "total_deals": len(deals),
        "closed": sum(1 for deal in deals if deal["operational_state"] == "CLOSED"),
        "held": sum(1 for deal in deals if deal["operational_state"] == "HELD_FRAUD"),
        "flagged": sum(1 for deal in deals if deal["fraud"].get("flagged")),
        "waiting_human": waiting,
        "alerts": alerts,
        "deals": deals,
    }


def pending_requests():
    with db.connect() as conn:
        rows = conn.execute(
            """SELECT hr.*, d.folder, d.stage, d.operational_state, d.extracted_json
               FROM human_requests hr JOIN deals d ON d.id=hr.deal_id
               WHERE hr.status='waiting' ORDER BY hr.requested_at"""
        ).fetchall()
        result = []
        for row in rows:
            item = dict(row)
            item["extracted"] = json.loads(item.pop("extracted_json") or "{}")
            item["required_role"] = ROLE_FOR_REQUEST.get(item["type"])
            result.append(item)
        return result


def learned_patterns():
    with db.connect() as conn:
        rows = conn.execute("SELECT * FROM fraud_patterns ORDER BY last_seen DESC").fetchall()
    return [
        {
            "signature": row["signature"], "name": row["name"],
            "features": json.loads(row["features_json"] or "[]"),
            "occurrences": row["occurrences"], "first_seen": row["first_seen"],
            "last_seen": row["last_seen"], "status": row["status"],
        }
        for row in rows
    ]


def export_submission(team="DealPilot", dataset="sample"):
    deals = get_deals()
    payload = {"team": team, "set": dataset, "deals": {}, "history_flags": []}
    for deal in deals:
        if not deal["id"].startswith("deal_"):
            continue
        full = get_deal(deal["id"])
        extracted = full["extracted"]
        payload["deals"][deal["folder"]] = {
            "extracted": {
                key: extracted.get(key)
                for key in (
                    "legal_name", "ein", "amount_requested", "purpose_category", "product",
                    "term_months", "guarantor_name", "annual_revenue_stated"
                )
            } | {"needed_by": full.get("needed_by")},
            "issues": full["issues"],
            "stage_log": [
                {key: row.get(key) for key in ("stage", "entered_at", "exited_at", "decision", "why")}
                for row in full["stage_log"]
            ],
            "human_requests": [
                {
                    "type": row["type"], "to": row["recipient"], "at": row["requested_at"],
                    "message": row["message"], "answer": row["answer"], "answered_at": row["answered_at"]
                }
                for row in reversed(full["human_requests"])
            ],
            "alerts": [
                {"at": row["at"], "stage": row["stage"], "level": row["level"], "to": row["recipient"], "message": row["message"]}
                for row in reversed(full["alerts"])
            ],
            "fraud": {
                "flagged": full["fraud"].get("flagged", False),
                "flagged_at": full["fraud"].get("flagged_at") or next((event["at"] for event in full["events"] if event["event_type"] == "FraudFindingUpdated"), None),
                "stage_at_flag": full["fraud"].get("stage_at_flag") or (full["stage"] if full["fraud"].get("flagged") else None),
                "held": full["fraud"].get("held", False),
                "reason": full["fraud"].get("reason"),
                "linked_deals": full["fraud"].get("linked_deals", []),
            },
        }
        payload["history_flags"].extend(
            linked for linked in full["fraud"].get("linked_deals", []) if linked.startswith("H-")
        )
    payload["history_flags"] = sorted(set(payload["history_flags"]))
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    path = OUTPUT_DIR / "submission.json"
    path.write_text(json.dumps(payload, indent=2))
    return payload, path

