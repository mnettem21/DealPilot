import base64
import hashlib
import json
import os
import re
import secrets
from pathlib import Path

from . import db, service
from .config import DATA_DIR, SIM_START


UPLOAD_ROOT = DATA_DIR / "uploads"
MAX_UPLOAD_BYTES = 10 * 1024 * 1024
ALLOWED_EXTENSIONS = {".pdf", ".csv", ".jpg", ".jpeg", ".png"}
REQUIRED_DOCUMENTS = [
    {"type": "guarantor_id", "label": "Government photo ID", "stage": "Identity"},
    {"type": "certificate_of_formation", "label": "Certificate of formation", "stage": "Business"},
    {"type": "ein_letter", "label": "IRS EIN letter", "stage": "Business"},
    {"type": "bank_statement", "label": "Six recent bank statements", "stage": "Financial"},
    {"type": "financial_statements", "label": "P&L and balance sheet", "stage": "Financial"},
    {"type": "tax_return", "label": "Latest tax return or eligible extension", "stage": "Financial"},
]


def _app_id():
    return "app_" + secrets.token_hex(8)


def _safe_filename(value):
    name = Path(value or "upload").name
    return re.sub(r"[^A-Za-z0-9._ -]", "_", name)[:120]


def _application_row(conn, application_id, user_id):
    row = conn.execute(
        "SELECT * FROM borrower_applications WHERE id=? AND user_id=?",
        (application_id, user_id),
    ).fetchone()
    if not row:
        raise ValueError("Application not found")
    return row


def _decode_application(row):
    item = dict(row)
    for key in ("form_json", "score_json", "decision_json"):
        item[key.removesuffix("_json")] = json.loads(item.pop(key) or "{}")
    return item


def create_application(user):
    identifier = _app_id()
    deal_identifier = "portal_" + identifier.removeprefix("app_")
    organization = re.sub(r"\s+", " ", user.get("organization") or "New business").strip()
    folder = f"Portal - {organization} - {identifier[-4:]}"
    now = service.sim_now()
    with db.transaction() as conn:
        conn.execute(
            """INSERT INTO deals(
                 id, folder, stage, operational_state, entered_at, evidence_version,
                 extracted_json, issues_json, fraud_json, updated_at
               ) VALUES(?,?,'Intake','DRAFT',?,0,'{}','[]','{}',?)""",
            (deal_identifier, folder, now, now),
        )
        conn.execute(
            "INSERT INTO stage_events(deal_id,stage,entered_at,waiting_on) VALUES(?,'Intake',?,'borrower submission')",
            (deal_identifier, now),
        )
        conn.execute(
            """INSERT INTO borrower_applications(
                 id,user_id,deal_id,status,current_step,form_json,created_at,updated_at
               ) VALUES(?,?,?,'draft',1,?,?,?)""",
            (identifier, user["id"], deal_identifier, json.dumps({"legal_name": organization}), now, now),
        )
        db.event(conn, "BorrowerApplicationStarted", deal_identifier, actor=user["id"], at=now)
    return get_application(identifier, user["id"])


def list_applications(user_id):
    with db.connect() as conn:
        rows = conn.execute(
            "SELECT * FROM borrower_applications WHERE user_id=? ORDER BY created_at DESC",
            (user_id,),
        ).fetchall()
    return [_decorate_application(_decode_application(row)) for row in rows]


def get_application(application_id, user_id):
    with db.connect() as conn:
        item = _decode_application(_application_row(conn, application_id, user_id))
    return _decorate_application(item)


def _decorate_application(item):
    deal = service.get_deal(item["deal_id"]) if item.get("deal_id") else None
    item["deal"] = deal
    item["required_documents"] = REQUIRED_DOCUMENTS
    item["uploaded_documents"] = (deal or {}).get("documents", [])
    return item


def save_application(application_id, user_id, form, current_step=None):
    if not isinstance(form, dict):
        raise ValueError("Application form must be an object")
    with db.transaction() as conn:
        row = _application_row(conn, application_id, user_id)
        if row["status"] not in {"draft", "needs_information"}:
            raise ValueError("Submitted applications cannot be edited")
        existing = json.loads(row["form_json"] or "{}")
        allowed = {
            "legal_name", "dba", "ein", "formation_date", "business_address", "industry",
            "annual_revenue_stated", "bank_account", "amount_requested", "product", "purpose",
            "term_months", "needed_by", "guarantor_name", "guarantor_dob",
            "guarantor_address", "guarantor_dl_number", "purpose_category",
        }
        for key, value in form.items():
            if key in allowed:
                existing[key] = value.strip() if isinstance(value, str) else value
        step = max(1, min(5, int(current_step or row["current_step"])))
        now = service.sim_now(conn)
        conn.execute(
            "UPDATE borrower_applications SET form_json=?,current_step=?,updated_at=? WHERE id=?",
            (json.dumps(existing), step, now, application_id),
        )
        db.event(conn, "BorrowerApplicationSaved", row["deal_id"], actor=user_id, payload={"step": step}, at=now)
    return get_application(application_id, user_id)


def upload_document(application_id, user_id, filename, encoded):
    filename = _safe_filename(filename)
    extension = Path(filename).suffix.lower()
    if extension not in ALLOWED_EXTENSIONS:
        raise ValueError("Upload a PDF, CSV, JPG, JPEG, or PNG file")
    try:
        raw = base64.b64decode(encoded or "", validate=True)
    except Exception:
        raise ValueError("The uploaded file could not be decoded") from None
    if not raw or len(raw) > MAX_UPLOAD_BYTES:
        raise ValueError("Files must be between 1 byte and 10 MB")

    with db.connect() as conn:
        row = _application_row(conn, application_id, user_id)
        if row["status"] not in {"draft", "needs_information"}:
            raise ValueError("This application no longer accepts uploads")
        deal_id = row["deal_id"]
    folder = UPLOAD_ROOT / user_id / application_id
    folder.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(UPLOAD_ROOT, 0o700)
        os.chmod(UPLOAD_ROOT / user_id, 0o700)
        os.chmod(folder, 0o700)
    except OSError:
        pass
    destination = folder / f"{secrets.token_hex(4)}_{filename}"
    destination.write_bytes(raw)
    try:
        os.chmod(destination, 0o600)
    except OSError:
        pass
    try:
        result = service.process_portal_document(deal_id, destination, filename, user_id)
    except Exception:
        destination.unlink(missing_ok=True)
        raise
    return {"document": result, "application": get_application(application_id, user_id)}


def _number(value, field):
    try:
        return float(str(value).replace(",", "").replace("$", ""))
    except (TypeError, ValueError):
        raise ValueError(f"Enter a valid {field}") from None


def _application_facts(form):
    required = [
        "legal_name", "ein", "formation_date", "business_address", "annual_revenue_stated",
        "amount_requested", "product", "purpose", "term_months", "guarantor_name",
        "guarantor_dob", "guarantor_address", "guarantor_dl_number",
    ]
    missing = [field.replace("_", " ") for field in required if not form.get(field)]
    if missing:
        raise ValueError("Complete these fields before submitting: " + ", ".join(missing))
    facts = dict(form)
    facts["amount_requested"] = _number(form.get("amount_requested"), "loan amount")
    facts["annual_revenue_stated"] = _number(form.get("annual_revenue_stated"), "annual revenue")
    facts["term_months"] = int(_number(form.get("term_months"), "term"))
    facts["guarantor_dl_number"] = re.sub(r"\D", "", str(form.get("guarantor_dl_number")))
    if not facts.get("purpose_category"):
        purpose = str(facts.get("purpose", "")).lower()
        facts["purpose_category"] = (
            "equipment" if "equipment" in purpose else "payroll" if "payroll" in purpose
            else "inventory" if "inventory" in purpose else "refinance" if "refinanc" in purpose
            else "expansion" if any(word in purpose for word in ("expand", "build out", "second location"))
            else "working_capital"
        )
    return facts


def submit_application(application_id, user):
    with db.transaction() as conn:
        row = _application_row(conn, application_id, user["id"])
        if row["status"] not in {"draft", "needs_information"}:
            raise ValueError("Application has already been submitted")
        form = json.loads(row["form_json"] or "{}")
        facts = _application_facts(form)
        now = service.sim_now(conn)
        serialized = json.dumps(facts, sort_keys=True)
        digest = hashlib.sha256(serialized.encode()).hexdigest()
        path = f"portal://{application_id}/application.json"
        doc_id = "doc_" + hashlib.sha256(f"{application_id}:{digest}".encode()).hexdigest()[:16]
        existing = conn.execute("SELECT id FROM documents WHERE path=?", (path,)).fetchone()
        if existing:
            conn.execute(
                "UPDATE documents SET checksum=?,facts_json=?,arrived_at=?,created_at=? WHERE id=?",
                (digest, serialized, now, now, existing["id"]),
            )
        else:
            conn.execute(
                """INSERT OR REPLACE INTO documents(
                     id,deal_id,path,filename,checksum,arrived_at,document_type,status,
                     page_count,producer,extracted_text,facts_json,created_at
                   ) VALUES(?,?,?,?,?,?,'loan_application','PROCESSED',1,'DealPilot Portal','',?,?)""",
                (doc_id, row["deal_id"], path, "Digital Loan Application", digest, now, serialized, now),
            )
        conn.execute(
            "UPDATE deals SET evidence_version=evidence_version+1,operational_state='PROCESSING',updated_at=? WHERE id=?",
            (now, row["deal_id"]),
        )
        conn.execute(
            "UPDATE borrower_applications SET status='submitted',current_step=5,submitted_at=?,updated_at=? WHERE id=?",
            (now, now, application_id),
        )
        db.event(conn, "BorrowerApplicationSubmitted", row["deal_id"], actor=user["id"], at=now)

    with db.transaction() as conn:
        service.recompute_deal(conn, row["deal_id"])
        service.recompute_fraud(conn)
    service.evaluate_all()
    deal = service.get_deal(row["deal_id"])
    score, decision = score_application(deal, user)
    with db.transaction() as conn:
        conn.execute(
            "UPDATE borrower_applications SET score_json=?,decision_json=?,status=?,updated_at=? WHERE id=?",
            (json.dumps(score), json.dumps(decision), decision["application_status"], service.sim_now(conn), application_id),
        )
        db.event(conn, "ApplicationScored", row["deal_id"], actor="decision.engine", payload={"score": score, "decision": decision}, at=service.sim_now(conn))
    return get_application(application_id, user["id"])


def score_application(deal, user):
    score = 100
    reasons = []
    penalties = {"MISSING_DOC:": 8, "MISSING_PAGE:": 8, "MISMATCH:": 20, "OLD_TRICK:": 25}
    for issue in deal.get("issues", []):
        penalty = next((value for prefix, value in penalties.items() if issue.startswith(prefix)), 5)
        score -= penalty
        reasons.append({"code": issue, "impact": -penalty})
    if deal.get("fraud", {}).get("flagged"):
        score -= 55
        reasons.append({"code": "FRAUD_NETWORK_MATCH", "impact": -55})
    score = max(0, min(100, score))
    amount = float(deal.get("extracted", {}).get("amount_requested") or 0)
    if score >= 90:
        tier = "FAST_TRACK"
        application_status = "prequalified"
        recommendation = "Pre-qualified for the requested amount, subject to required bank approvals and final verification."
        suggested_amount = amount
    elif score >= 70:
        tier = "HUMAN_REVIEW"
        application_status = "human_review"
        suggested_amount = round(amount * 0.5, 2)
        recommendation = "Route to a bank reviewer. Consider up to 50% of the requested amount while exceptions are resolved."
    else:
        tier = "DECLINE_RECOMMENDED"
        application_status = "decline_recommended"
        suggested_amount = 0
        recommendation = "Decline recommendation. A bank reviewer must confirm the reasons before sending the notice."
    score_payload = {
        "approval_readiness": score,
        "tier": tier,
        "suggested_amount": suggested_amount,
        "reasons": reasons,
        "model": "explainable-rules-v1",
        "protected_attributes_used": False,
    }
    if score < 70:
        improvement_steps = []
        for reason in reasons:
            code = reason["code"]
            if "MISSING_DOC" in code or "MISSING_PAGE" in code:
                doc = code.split(":", 1)[-1].replace("_", " ")
                improvement_steps.append(f"• Documentation: Provide complete business records ({doc}).")
            elif "MISMATCH" in code:
                field_name = code.split(":", 1)[-1].replace("_", " ")
                improvement_steps.append(f"• Discrepancies: Resolve factual mismatch regarding {field_name} across legal and application records.")
            elif "REV_INFLATION" in code:
                improvement_steps.append("• Cash Flow: Ensure stated annual revenue aligns with verifiable deposit activity across bank statements.")
            elif "STMT_TAMPER" in code:
                improvement_steps.append("• Verification: Provide certified, unedited bank statements with matching month-to-month opening/closing balances.")
        if not improvement_steps:
            improvement_steps.append("• Financial History: Provide complete operating bank statements and audited financial statements.")

        improvement_text = "\n".join(improvement_steps[:4])
        email_draft = (
            f"Hello {user['display_name']},\n\n"
            f"Thank you for applying for commercial financing on behalf of {user.get('organization') or 'your business'}. "
            f"Based on our initial underwriting review, your current Approval-Readiness Score is {score}/100 (Tier: Decline Recommended).\n\n"
            "Key areas requiring improvement before approval can be considered:\n"
            f"{improvement_text}\n\n"
            "Timeline & roadmap to improve your score for future applications:\n"
            "• Immediate Documentation Cure (30 Days): If any missing documents or discrepancies can be resolved with official supporting records, you may provide them within 30 days for administrative reconsideration.\n"
            "• Operational History (3–6 Months): Demonstrating 3 to 6 consecutive months of stable operating deposits and debt-service coverage will significantly elevate your readiness score.\n"
            "• Reapplication Window (90 Days): You are eligible to submit a fresh application after 90 days with updated financial statements.\n\n"
            "Note: This draft must be reviewed by a bank employee before it is sent.\n\n"
            "Sincerely,\n"
            "Trinity Commercial Bank Commercial Lending Team"
        )
    elif score < 90:
        email_draft = (
            f"Hello {user['display_name']},\n\n"
            f"Thank you for applying for commercial financing on behalf of {user.get('organization') or 'your business'}. "
            f"Your application received an Approval-Readiness Score of {score}/100 (Tier: Human Review).\n\n"
            f"Conditional Approval Offer (50% Facility):\n"
            f"Based on our initial evaluation, we are pleased to conditionally recommend approval for a partial loan facility of up to 50% of your requested amount: ${suggested_amount:,.2f} (requested: ${amount:,.2f}). "
            "This structured facility allows your business to access immediate financing while managing credit exceptions.\n\n"
            f"Pathway & Approval Rate for Full Facility (${amount:,.2f}):\n"
            "• Historical Full-Approval Rate: Borrowers who accept the 50% facility and establish 6 consecutive months of prompt, on-time repayment achieve an estimated 85%–90% approval rate when requesting an expansion to the full requested amount.\n"
            "• Immediate Full-Amount Qualification: You may qualify for the full requested amount sooner by resolving outstanding documentation exceptions or submitting audited fiscal year-end financials for Credit Committee review.\n\n"
            "A Trinity Commercial Bank credit officer will be in touch shortly to discuss these options and finalize your terms.\n\n"
            "Note: This draft must be reviewed by a bank employee before it is sent.\n\n"
            "Sincerely,\n"
            "Trinity Commercial Bank Commercial Lending Team"
        )
    else:
        email_draft = (
            f"Hello {user['display_name']},\n\n"
            f"Congratulations! Your application for {user.get('organization') or 'your business'} has achieved an Approval-Readiness Score of {score}/100 (Tier: Fast Track).\n\n"
            f"You have been pre-qualified for the full requested amount of ${amount:,.2f}. "
            "Our closing team is preparing your commitment letter and final disbursement schedule.\n\n"
            "Sincerely,\n"
            "Trinity Commercial Bank Commercial Lending Team"
        )

    decision = {
        "application_status": application_status,
        "recommendation": recommendation,
        "requires_human_confirmation": True,
        "reapply_after_days": 90 if score < 70 else None,
        "email_subject": "Update on your Trinity Commercial Bank application",
        "email_draft": email_draft,
    }
    return score_payload, decision


def manifest(application_id, user_id):
    application = get_application(application_id, user_id)
    deal = application.get("deal") or {}
    return {
        "schema_version": "1.0",
        "application_id": application["id"],
        "status": application["status"],
        "submitted_at": application.get("submitted_at"),
        "borrower": application["form"],
        "documents": [
            {
                "id": document["id"], "filename": document["filename"],
                "type": document["document_type"], "status": document["status"],
                "checksum_sha256": document["checksum"], "arrived_at": document["arrived_at"],
            }
            for document in application["uploaded_documents"]
        ],
        "validation_issues": deal.get("issues", []),
        "approval_score": application["score"],
        "decision": application["decision"],
        "audit": {"evidence_version": deal.get("evidence_version"), "stage": deal.get("stage")},
    }

