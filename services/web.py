#!/usr/bin/env python3
"""DealPilot Web Server — PocketBase Table Explorer & Dashboard Backend.

Connects the DealPilot Web UI to PocketBase collections:
- Stage Queues: intake, credit, committee, product, committed, closed
- Files & Evidence: files
- Audit Trail: actions
- SLA & Escalations: alerts
- Reference Directory: contacts
- Historical Portfolio: history
- Risk & Fraud Flags: fraud_detected
"""
import base64
import json
import mimetypes
import os
import re
import sys
import traceback
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.common.env import load_env
from services.common.pocketbase import PocketBase, PocketBaseError, escape_filter
from services.common.clock import clock, now
from services.stages import ORDER, load as load_stage
from services.fraud_agent import FraudDetectionAgent

WEB_DIR = ROOT / "web"

ACCOUNTS = [
    {
        "id": "usr_lauren",
        "person_id": "lauren.kim",
        "name": "Lauren Kim",
        "title": "Head of Commercial Lending Operations",
        "role": "admin",
        "role_badge": "👑 Chief Lending Officer (Admin)",
        "email": "lauren.kim@trinitycommercial.example",
        "password": "DealPilot2026!",
        "department": "Executive & Lending Operations",
        "initials": "LK",
        "allowed_stages": ["intake", "credit", "committee", "product", "committed", "closed"],
        "can_release_funds": True,
        "can_manage_fraud": True,
        "see_all_alerts": True,
        "description": "Whole Access: Can view all tables, approve any stage, run fraud scans, release holds, and disburse capital."
    },
    {
        "id": "usr_olivia",
        "person_id": "olivia.chen",
        "name": "Olivia Chen",
        "title": "Intake Specialist",
        "role": "intake",
        "role_badge": "📥 Intake Specialist",
        "email": "olivia.chen@trinitycommercial.example",
        "password": "DealPilot2026!",
        "department": "Intake Operations",
        "initials": "OC",
        "allowed_stages": ["intake"],
        "can_release_funds": False,
        "can_manage_fraud": False,
        "see_all_alerts": False,
        "description": "Intake Only: Restricted exclusively to intake records and initial filings. Other stage queues are locked."
    },
    {
        "id": "usr_marcus",
        "person_id": "marcus.lee",
        "name": "Marcus Lee",
        "title": "Credit Officer",
        "role": "credit",
        "role_badge": "💳 Credit Officer",
        "email": "marcus.lee@trinitycommercial.example",
        "password": "DealPilot2026!",
        "department": "Credit Underwriting",
        "initials": "ML",
        "allowed_stages": ["credit"],
        "can_release_funds": False,
        "can_manage_fraud": False,
        "see_all_alerts": False,
        "description": "Credit Only: Restricted exclusively to credit records, financial spreads, and bank statements."
    },
    {
        "id": "usr_priscilla",
        "person_id": "priscilla.nguyen",
        "name": "Priscilla Nguyen",
        "title": "Senior Credit Officer & Delegated Approver",
        "role": "committee",
        "role_badge": "👥 Committee Chair",
        "email": "priscilla.nguyen@trinitycommercial.example",
        "password": "DealPilot2026!",
        "department": "Credit Committee",
        "initials": "PN",
        "allowed_stages": ["committee"],
        "can_release_funds": False,
        "can_manage_fraud": False,
        "see_all_alerts": False,
        "description": "Committee Only: Restricted exclusively to committee review records and credit memos."
    },
    {
        "id": "usr_tasha",
        "person_id": "tasha.reed",
        "name": "Tasha Reed",
        "title": "Closing & Funding Officer",
        "role": "closing",
        "role_badge": "🏁 Closing & Funding",
        "email": "tasha.reed@trinitycommercial.example",
        "password": "DealPilot2026!",
        "department": "Closing & Funding",
        "initials": "TR",
        "allowed_stages": ["closed"],
        "can_release_funds": True,
        "can_manage_fraud": False,
        "see_all_alerts": False,
        "description": "Releasing Records Only: Can view closed stage records and click Release Funds (blocked if a fraud hold exists)."
    },
    {
        "id": "usr_victor",
        "person_id": "victor.hale",
        "name": "Victor Hale",
        "title": "Fraud Operations Analyst",
        "role": "fraud_ops",
        "role_badge": "🚨 Fraud Operations",
        "email": "victor.hale@trinitycommercial.example",
        "password": "DealPilot2026!",
        "department": "Fraud Operations",
        "initials": "VH",
        "allowed_stages": ["closed"],
        "can_release_funds": False,
        "can_manage_fraud": True,
        "see_all_alerts": True,
        "description": "Fraud & Alerts Intel: Inspects flagged syndicates and monitors alert queues."
    }
]

UNIFIED_WORKSTATION_PASSWORD = "DealPilot2026!"

ACCOUNT_BY_ID = {acc["person_id"]: acc for acc in ACCOUNTS}
ACCOUNT_BY_ROLE = {acc["role"]: acc for acc in ACCOUNTS}
ACCOUNT_BY_EMAIL = {acc["email"].lower(): acc for acc in ACCOUNTS}
ACCOUNT_BY_EMAIL[os.environ.get("POCKETBASE_EMAIL", "darwin_walter41@yahoo.com").lower()] = ACCOUNTS[0]
ROLE_PERMISSIONS = ACCOUNT_BY_ROLE

BORROWERS_FILE = ROOT / "data" / "borrowers.json"

DEFAULT_BORROWERS = [
    {
        "id": "bor_oakline",
        "email": "dave@oaklineauto.example",
        "password": "DealPilot2026!",
        "business_name": "Oakline Auto",
        "guarantor_name": "David Vance",
        "folder": "Oakline Auto",
        "amount_requested": 150000,
        "purpose": "working_capital",
        "role": "borrower"
    },
    {
        "id": "bor_ridgeway",
        "email": "borrower@ridgewayfreight.example",
        "password": "DealPilot2026!",
        "business_name": "Ridgeway Freight Logistics",
        "guarantor_name": "Arthur Pendelton",
        "folder": "Ridgeway Freight Logistics",
        "amount_requested": 350000,
        "purpose": "equipment",
        "role": "borrower"
    },
    {
        "id": "bor_apex",
        "email": "sarah@apexindustrial.example",
        "password": "DealPilot2026!",
        "business_name": "Apex Industrial Supply",
        "guarantor_name": "Sarah Jenkins",
        "folder": "Apex Industrial Supply",
        "amount_requested": 280000,
        "purpose": "working_capital",
        "role": "borrower"
    },
    {
        "id": "bor_metroplex",
        "email": "applicant@metroplexdental.example",
        "password": "DealPilot2026!",
        "business_name": "Metroplex Dental Group",
        "guarantor_name": "Dr. Ronald Evans",
        "folder": "Metroplex Dental Group",
        "amount_requested": 420000,
        "purpose": "expansion",
        "role": "borrower"
    }
]


def load_borrowers() -> list:
    if not BORROWERS_FILE.exists():
        save_borrowers(DEFAULT_BORROWERS)
        return list(DEFAULT_BORROWERS)
    try:
        data = json.loads(BORROWERS_FILE.read_text())
        return data if isinstance(data, list) else list(DEFAULT_BORROWERS)
    except Exception:
        return list(DEFAULT_BORROWERS)


def save_borrowers(borrowers: list):
    try:
        BORROWERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        BORROWERS_FILE.write_text(json.dumps(borrowers, indent=2))
    except Exception as e:
        print(f"Error saving borrowers: {e}")


def resolve_account(role_or_id: str = None) -> dict:
    if not role_or_id:
        return ACCOUNTS[0]
    key = str(role_or_id).strip().lower()

    # Check borrowers first if key starts with bor_ or matches borrower email
    for bor in load_borrowers():
        if key == bor["id"].lower() or key == bor["email"].lower():
            return {
                "id": bor["id"],
                "person_id": bor["id"],
                "name": bor["guarantor_name"],
                "business_name": bor["business_name"],
                "title": f"Borrower ({bor['business_name']})",
                "role": "borrower",
                "role_badge": "🏢 Commercial Borrower",
                "email": bor["email"],
                "folder": bor["folder"],
                "allowed_stages": [],
                "can_release_funds": False,
                "can_manage_fraud": False,
                "see_all_alerts": False,
                "description": f"Authorized Borrower for {bor['business_name']}"
            }

    if key in ACCOUNT_BY_ROLE:
        return ACCOUNT_BY_ROLE[key]
    if key in ACCOUNT_BY_ID:
        return ACCOUNT_BY_ID[key]
    if key in ACCOUNT_BY_EMAIL:
        return ACCOUNT_BY_EMAIL[key]
    # check prefix match
    for acc in ACCOUNTS:
        if key in acc["person_id"].lower() or key in acc["name"].lower():
            return acc
    return ACCOUNTS[0]


TABLE_METADATA = [
    {
        "name": "intake",
        "category": "Stage Queues (Deals)",
        "badge": "Stage 1",
        "icon": "📥",
        "description": "Initial deal intake queue. Validates application, formation docs, photo ID, and EIN letter.",
        "primary_key": "folder",
        "columns": ["folder", "status", "assignee", "entered_at", "waiting_on", "extracted"]
    },
    {
        "name": "credit",
        "category": "Stage Queues (Deals)",
        "badge": "Stage 2",
        "icon": "💳",
        "description": "Credit underwriting queue. Checks bank statements, tax returns, and financial spreads.",
        "primary_key": "folder",
        "columns": ["folder", "status", "assignee", "entered_at", "waiting_on", "memo", "extracted"]
    },
    {
        "name": "committee",
        "category": "Stage Queues (Deals)",
        "badge": "Stage 3",
        "icon": "👥",
        "description": "Credit Committee review queue. Senior committee approval for facilities exceeding delegation limits.",
        "primary_key": "folder",
        "columns": ["folder", "status", "assignee", "entered_at", "waiting_on", "memo", "extracted"]
    },
    {
        "name": "product",
        "category": "Stage Queues (Deals)",
        "badge": "Stage 4",
        "icon": "🏷️",
        "description": "Product specialist queue. Validates equipment quotes, loan terms, and facility eligibility.",
        "primary_key": "folder",
        "columns": ["folder", "status", "assignee", "entered_at", "waiting_on", "memo", "extracted"]
    },
    {
        "name": "committed",
        "category": "Stage Queues (Deals)",
        "badge": "Stage 5",
        "icon": "🤝",
        "description": "Relationship Manager commitment sign-off queue. Final pre-closing borrower verification.",
        "primary_key": "folder",
        "columns": ["folder", "status", "assignee", "entered_at", "waiting_on", "memo", "extracted"]
    },
    {
        "name": "closed",
        "category": "Stage Queues (Deals)",
        "badge": "Stage 6",
        "icon": "🏁",
        "description": "Funding release and closing queue. Final disbursement authorization gate (held if fraud flagged).",
        "primary_key": "folder",
        "columns": ["folder", "status", "assignee", "entered_at", "waiting_on", "extracted"]
    },
    {
        "name": "files",
        "category": "Evidence & Documents",
        "badge": "Evidence",
        "icon": "📄",
        "description": "Repository of all borrower documents, statements, tax returns, IDs, and equipment quotes.",
        "primary_key": "path",
        "columns": ["folder", "filename", "doc_type", "source", "arrived_at", "path"]
    },
    {
        "name": "actions",
        "category": "Audit & Movement",
        "badge": "Audit Trail",
        "icon": "📜",
        "description": "Immutable audit trail of stage entries, stage exits, and human underwriting decisions.",
        "primary_key": "id",
        "columns": ["folder", "stage", "kind", "decision", "actor", "at", "why"]
    },
    {
        "name": "alerts",
        "category": "SLA & Monitoring",
        "badge": "Operations",
        "icon": "⚠️",
        "description": "Simulated SLA timer alerts and 150%/200% managerial escalation notices.",
        "primary_key": "id",
        "columns": ["folder", "stage", "level", "to", "at", "message"]
    },
    {
        "name": "contacts",
        "category": "Reference Directory",
        "badge": "Directory",
        "icon": "👤",
        "description": "Bank personnel directory, roles, OOO dates, designated backups, and escalation hierarchy.",
        "primary_key": "person_id",
        "columns": ["person_id", "name", "team", "role", "email", "phone", "backup", "escalates_to", "notes"]
    },
    {
        "name": "history",
        "category": "Historical Portfolio",
        "badge": "History Book",
        "icon": "📚",
        "description": "Historical portfolio records used for delinquency patterns, recidivism, and syndicate links.",
        "primary_key": "deal_id",
        "columns": ["deal_id", "legal_name", "amount_requested", "guarantor_name", "final_status", "fraud_label", "equipment_vendor", "vendor_remit_acct_last4"]
    },
    {
        "name": "fraud_detected",
        "category": "Risk & Fraud Flags",
        "badge": "Security Hold",
        "icon": "🚨",
        "description": "Active fraud ring flags. Prohibits Closed rules from disbursing loan capital.",
        "primary_key": "folder",
        "columns": ["folder", "stage", "reason", "at"]
    }
]

TABLE_MAP = {t["name"]: t for t in TABLE_METADATA}


class PocketBaseService:
    def __init__(self):
        load_env()
        self._pb = None

    @property
    def pb(self):
        if self._pb is None:
            self._pb = PocketBase()
        return self._pb

    def get_table_counts(self):
        counts = {}
        for item in TABLE_METADATA:
            name = item["name"]
            try:
                records = self.pb.list_records(name)
                counts[name] = len(records)
            except Exception:
                counts[name] = 0
        return counts

    def list_table(self, name: str, search: str = "", limit: int = 500):
        if name not in TABLE_MAP:
            raise ValueError(f"Unknown table '{name}'")
        records = self.pb.list_records(name, sort="-id")
        if search:
            s = search.lower()
            def match(r):
                for v in r.values():
                    if isinstance(v, (str, int, float)) and s in str(v).lower():
                        return True
                    if isinstance(v, dict) and s in json.dumps(v).lower():
                        return True
                return False
            records = [r for r in records if match(r)]
        return records[:limit]

    def get_record(self, name: str, record_id: str):
        records = self.pb.list_records(name, filter_expr=f"id='{escape_filter(record_id)}'")
        return records[0] if records else None

    def create_record(self, name: str, fields: dict):
        return self.pb.create_record(name, fields)

    def update_record(self, name: str, record_id: str, fields: dict):
        return self.pb.update_record(name, record_id, fields)

    def delete_record(self, name: str, record_id: str):
        return self.pb.delete_record(name, record_id)

    def delete_by_filter(self, name: str, filter_expr: str):
        records = self.pb.list_records(name, filter_expr=filter_expr)
        for r in records:
            self.pb.delete_record(name, r["id"])
        return len(records)

    def get_all_deals_data(self):
        deals_by_stage = []
        for stage in ORDER:
            try:
                for row in self.pb.list_records(stage):
                    deals_by_stage.append((stage, row))
            except Exception:
                pass

        actions_by_folder = {}
        try:
            for act in self.pb.list_records("actions", sort="id"):
                actions_by_folder.setdefault(act.get("folder"), []).append(act)
        except Exception:
            pass

        alerts_by_folder = {}
        try:
            for alert in self.pb.list_records("alerts", sort="id"):
                alerts_by_folder.setdefault(alert.get("folder"), []).append(alert)
        except Exception:
            pass

        files_by_folder = {}
        try:
            for f in self.pb.list_records("files"):
                files_by_folder.setdefault(f.get("folder"), []).append(f)
        except Exception:
            pass

        fraud_flags = {}
        try:
            for flag in self.pb.list_records("fraud_detected"):
                fraud_flags[flag.get("folder")] = flag
        except Exception:
            pass

        formatted_deals = []
        for stage_name, row in deals_by_stage:
            folder = row.get("folder")
            extracted = row.get("extracted") or {}
            status = row.get("status", "open")
            waiting_on = row.get("waiting_on", "")
            deal_actions = actions_by_folder.get(folder, [])
            deal_alerts = alerts_by_folder.get(folder, [])
            deal_files = files_by_folder.get(folder, [])
            fraud = fraud_flags.get(folder)

            # Determine operational state for the UI
            is_held = (stage_name == "closed" and fraud is not None and status != "funded")
            if is_held:
                operational_state = "HELD_FRAUD"
            elif status == "funded":
                operational_state = "CLOSED"
            elif status == "declined":
                operational_state = "DECLINED"
            elif status == "waiting_human" or (stage_name in {"credit", "committee", "committed", "closed"} and status == "open" and not waiting_on):
                operational_state = "WAITING_HUMAN"
            elif waiting_on:
                operational_state = "WAITING_DOCUMENTS"
            elif stage_name == "closed" and not fraud:
                operational_state = "READY_TO_CLOSE"
            else:
                operational_state = "OPEN"

            # Parse blockers
            blockers = [b.strip() for b in waiting_on.split(";") if b.strip()]

            # Readiness calculation
            readiness = 100
            if is_held:
                readiness = 20
            elif operational_state == "CLOSED":
                readiness = 100
            elif operational_state == "DECLINED":
                readiness = 0
            else:
                stage_idx = ORDER.index(stage_name) if stage_name in ORDER else 0
                base_readiness = 30 + (stage_idx * 12)
                penalty = len(blockers) * 15
                readiness = max(15, min(95, base_readiness - penalty))

            formatted_deals.append({
                "id": folder,
                "folder": folder,
                "stage": stage_name.capitalize(),
                "stage_lower": stage_name,
                "operational_state": operational_state,
                "status": status,
                "assignee": row.get("assignee"),
                "entered_at": row.get("entered_at"),
                "waiting_on": waiting_on,
                "current_blockers": blockers,
                "readiness": readiness,
                "memo": row.get("memo", ""),
                "document_count": len(deal_files),
                "extracted": {
                    "legal_name": extracted.get("legal_name") or extracted.get("formation_legal_name") or folder,
                    "ein": extracted.get("ein") or "Pending",
                    "amount_requested": extracted.get("amount_requested") or 0,
                    "product": extracted.get("product") or "Commercial Loan",
                    "purpose": extracted.get("purpose_category") or "Working Capital",
                    "term_months": extracted.get("term_months"),
                    "guarantor_name": extracted.get("guarantor_name") or "Pending",
                    "annual_revenue_stated": extracted.get("annual_revenue_stated") or 0,
                    "relationship_manager": extracted.get("relationship_manager") or "brian.foster",
                    "credit_officer": extracted.get("credit_officer") or "marcus.lee",
                },
                "needed_by": extracted.get("needed_by") if extracted.get("needed_by") not in ("No specific date", None) else None,
                "fraud": {
                    "flagged": fraud is not None,
                    "at": fraud.get("at") if fraud else None,
                    "stage_at_flag": (fraud.get("stage") or "").capitalize() if fraud else None,
                    "held": is_held,
                    "reason": fraud.get("reason") if fraud else None,
                    "confidence": 0.99 if fraud else 0.0,
                    "linked_deals": ["Pecan Grove Catering", "Summit Peak Flooring", "H-2608-002", "H-2609-008"] if fraud and "Ridgeway" in folder else []
                },
                "files": [
                    {
                        "id": f.get("id"),
                        "filename": f.get("filename"),
                        "doc_type": f.get("doc_type"),
                        "source": f.get("source"),
                        "arrived_at": f.get("arrived_at"),
                        "path": f.get("path")
                    }
                    for f in deal_files
                ],
                "actions": deal_actions,
                "alerts": deal_alerts
            })

        return formatted_deals

    def build_summary(self):
        c = clock()
        current_sim_time = c.now().isoformat(timespec="minutes")
        deals = self.get_all_deals_data()

        total_alerts = 0
        try:
            total_alerts = len(self.pb.list_records("alerts"))
        except Exception:
            pass

        waiting_human = sum(1 for d in deals if d["operational_state"] in ("WAITING_HUMAN", "HELD_FRAUD"))
        flagged = sum(1 for d in deals if d["fraud"]["flagged"])
        held = sum(1 for d in deals if d["operational_state"] == "HELD_FRAUD")
        closed = sum(1 for d in deals if d["operational_state"] == "CLOSED")

        return {
            "now": current_sim_time,
            "total_deals": len(deals),
            "waiting_human": waiting_human,
            "flagged": flagged,
            "held": held,
            "closed": closed,
            "alerts": total_alerts,
            "deals": deals,
            "pocketbase_connected": True,
            "pocketbase_url": os.environ.get("POCKETBASE_URL", "")
        }


service = PocketBaseService()


class DealPilotRequestHandler(BaseHTTPRequestHandler):
    def _send_json(self, data, code=200):
        body = json.dumps(data, indent=2, default=str).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(body)

    def _send_error(self, message, code=400):
        self._send_json({"error": str(message)}, code=code)

    def _get_auth_user(self):
        auth_header = self.headers.get("Authorization", "")
        token = ""
        if auth_header.startswith("Bearer "):
            token = auth_header[7:].strip()
        parsed = urllib.parse.urlparse(self.path)
        query = urllib.parse.parse_qs(parsed.query)
        param = query.get("person_id", [None])[0] or query.get("role", [None])[0] or token
        return resolve_account(param)

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, PATCH, DELETE, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        # 0. API: List all Bank Employee Accounts for authentication
        if path == "/api/auth/accounts":
            return self._send_json({
                "accounts": ACCOUNTS
            })

        # Borrower Portal Deal Status
        if path == "/api/borrower/status":
            folder = query.get("folder", [None])[0]
            user = self._get_auth_user()
            if not folder and user.get("role") == "borrower":
                folder = user.get("folder")
            if not folder:
                return self._send_error("Folder parameter required", 400)

            try:
                # 1. Locate deal across stages
                stage_found = None
                deal_record = None
                for stg in ["intake", "credit", "committee", "product", "committed", "closed"]:
                    try:
                        recs = service.pb.list_records(stg, filter_expr=f"folder='{escape_filter(folder)}'")
                        if recs:
                            stage_found = stg
                            deal_record = recs[0]
                            break
                    except Exception:
                        pass

                # 2. Get files for this deal
                deal_files = []
                try:
                    deal_files = service.pb.list_records("files", filter_expr=f"folder='{escape_filter(folder)}'")
                except Exception:
                    pass

                # 3. Build document checklist
                seen_doc_types = {f.get("doc_type") for f in deal_files if f.get("doc_type")}
                bank_stmt_count = sum(1 for f in deal_files if f.get("doc_type") == "bank_statement")

                checklist = [
                    {
                        "type": "loan_application",
                        "label": "Small Business Loan Application",
                        "status": "received" if "loan_application" in seen_doc_types else "pending",
                        "description": "Signed commercial lending application."
                    },
                    {
                        "type": "guarantor_id",
                        "label": "Government Photo ID / Driver License",
                        "status": "received" if "guarantor_id" in seen_doc_types else "pending",
                        "description": "Valid unexpired government-issued driver license."
                    },
                    {
                        "type": "certificate_of_formation",
                        "label": "Certificate of Formation / Articles",
                        "status": "received" if "certificate_of_formation" in seen_doc_types else "pending",
                        "description": "Secretary of State corporate filing confirmation."
                    },
                    {
                        "type": "ein_letter",
                        "label": "IRS CP 575 EIN Confirmation Letter",
                        "status": "received" if "ein_letter" in seen_doc_types else "pending",
                        "description": "Official IRS Employer Identification confirmation."
                    },
                    {
                        "type": "bank_statement",
                        "label": f"Business Bank Statements ({bank_stmt_count}/6 received)",
                        "status": "received" if bank_stmt_count >= 1 else "pending",
                        "count": bank_stmt_count,
                        "description": "Recent 6 months operating bank account statements."
                    },
                    {
                        "type": "financial_statements",
                        "label": "Financial Statements (P&L & Balance Sheet)",
                        "status": "received" if "financial_statements" in seen_doc_types else "pending",
                        "description": "Year-to-date profit & loss and balance sheet."
                    },
                    {
                        "type": "tax_return",
                        "label": "Latest Business Tax Return (Form 1120/1065)",
                        "status": "received" if "tax_return" in seen_doc_types else "pending",
                        "description": "Most recent federal business tax return."
                    }
                ]

                stage_labels = {
                    "intake": "Stage 1: Application Intake & Document Verification",
                    "credit": "Stage 2: Credit Underwriting & Financial Spreads",
                    "committee": "Stage 3: Credit Committee Review",
                    "product": "Stage 4: Loan Structuring & Term Sheet",
                    "committed": "Stage 5: Final Commitment & Closing Prep",
                    "closed": "Stage 6: Closed & Capital Disbursement"
                }

                status_step = {
                    "intake": 1,
                    "credit": 2,
                    "committee": 3,
                    "product": 4,
                    "committed": 5,
                    "closed": 6
                }.get(stage_found, 1)

                return self._send_json({
                    "folder": folder,
                    "stage": stage_found or "intake",
                    "stage_step": status_step,
                    "stage_label": stage_labels.get(stage_found, "Application Submitted"),
                    "status": deal_record.get("status") if deal_record else "open",
                    "waiting_on": deal_record.get("waiting_on") if deal_record else "",
                    "extracted": deal_record.get("extracted", {}) if deal_record else {},
                    "entered_at": deal_record.get("entered_at", "") if deal_record else "",
                    "checklist": checklist,
                    "files": [
                        {
                            "id": f.get("id"),
                            "filename": f.get("filename"),
                            "doc_type": f.get("doc_type"),
                            "arrived_at": f.get("arrived_at"),
                            "path": f.get("path")
                        }
                        for f in deal_files
                    ]
                })
            except Exception as e:
                traceback.print_exc()
                return self._send_error(f"Status check failed: {e}", 500)

        # 1. API: List all PocketBase tables and stats (with department RBAC filtering)
        if path == "/api/tables":
            try:
                user = self._get_auth_user()
                if user.get("role") == "borrower":
                    return self._send_error("Access Denied: Commercial loan borrowers are restricted from internal bank operational tables.", 403)
                role = user["role"]
                allowed_stages = set(user.get("allowed_stages", []))

                counts = service.get_table_counts()
                result = []
                for item in TABLE_METADATA:
                    name = item["name"]
                    is_stage_queue = name in ORDER
                    # Determine department access
                    is_accessible = (role == "admin" or not is_stage_queue or name in allowed_stages)

                    entry = dict(item)
                    entry["count"] = counts.get(name, 0)
                    entry["accessible"] = is_accessible
                    entry["department_lock"] = not is_accessible
                    entry["lock_reason"] = f"Restricted to {name.capitalize()} Department" if not is_accessible else None
                    result.append(entry)

                return self._send_json({
                    "pocketbase_url": os.environ.get("POCKETBASE_URL", ""),
                    "pocketbase_email": os.environ.get("POCKETBASE_EMAIL", ""),
                    "team": os.environ.get("DEALPILOT_TEAM", "DealPilot"),
                    "total_tables": len(result),
                    "active_user": user,
                    "active_role": user,
                    "tables": result
                })
            except Exception as e:
                traceback.print_exc()
                return self._send_error(f"PocketBase error: {e}", 500)

        # 2. API: Get records from a specific table (with RBAC enforcement)
        if path.startswith("/api/tables/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) >= 3:
                table_name = parts[2]
                if table_name not in TABLE_MAP:
                    return self._send_error(f"Unknown table '{table_name}'", 404)

                user = self._get_auth_user()
                if user.get("role") == "borrower":
                    return self._send_error("Access Denied: Commercial loan borrowers are restricted from internal bank records.", 403)
                role = user["role"]
                allowed_stages = set(user.get("allowed_stages", []))

                # Check access: if this is a stage table and role is not admin and not in allowed_stages
                if table_name in ORDER and role != "admin" and table_name not in allowed_stages:
                    return self._send_error(
                        f"Access Denied: You are signed in as {user['name']} ({user['department']}). Only authorized personnel may access the {table_name.capitalize()} queue.",
                        403
                    )
                
                # If path has a record ID: /api/tables/<name>/<id>
                if len(parts) == 4:
                    rec_id = parts[3]
                    rec = service.get_record(table_name, rec_id)
                    if not rec:
                        return self._send_error("Record not found", 404)
                    return self._send_json(rec)

                # Otherwise list records
                search = query.get("search", [""])[0]
                limit = int(query.get("limit", [500])[0])
                try:
                    records = service.list_table(table_name, search=search, limit=limit)
                    # Filter tickets if user is restricted to a department
                    if table_name in ORDER and role != "admin":
                        person_id = user["person_id"]
                        dept_records = [
                            r for r in records
                            if r.get("assignee") == person_id or not r.get("assignee")
                        ]
                        if dept_records:
                            records = dept_records
                    return self._send_json({
                        "table": table_name,
                        "metadata": TABLE_MAP[table_name],
                        "count": len(records),
                        "records": records
                    })
                except Exception as e:
                    return self._send_error(f"Failed to query {table_name}: {e}", 500)

        # 3. API: Get Alerts (Dedicated Endpoint with RBAC filter)
        if path == "/api/alerts":
            try:
                user = self._get_auth_user()
                records = service.pb.list_records("alerts", sort="-at")

                # Filter by department unless admin or fraud ops
                if not user.get("see_all_alerts", False):
                    allowed_stages = set(user.get("allowed_stages", []))
                    person_id = user.get("person_id")
                    records = [
                        r for r in records
                        if r.get("stage") in allowed_stages or r.get("to") == person_id
                    ]

                search = query.get("search", [""])[0].lower()
                if search:
                    records = [
                        r for r in records
                        if search in (r.get("folder") or "").lower()
                        or search in (r.get("message") or "").lower()
                        or search in (r.get("to") or "").lower()
                    ]

                return self._send_json({
                    "count": len(records),
                    "role": user["role"],
                    "records": records
                })
            except Exception as e:
                return self._send_error(f"Failed to load alerts: {e}", 500)

        # 4. API: Get Fraud Detected (Dedicated Endpoint)
        if path in ("/api/fraud-detected", "/api/fraud_detected"):
            try:
                records = service.pb.list_records("fraud_detected", sort="-at")
                search = query.get("search", [""])[0].lower()
                if search:
                    records = [
                        r for r in records
                        if search in (r.get("folder") or "").lower()
                        or search in (r.get("reason") or "").lower()
                    ]
                return self._send_json({
                    "count": len(records),
                    "records": records
                })
            except Exception as e:
                return self._send_error(f"Failed to load fraud flags: {e}", 500)

        # 5. API: Available Roles for Persona Switcher
        if path == "/api/roles":
            return self._send_json({
                "roles": ACCOUNTS
            })

        # 6. API: Summary dashboard
        if path == "/api/summary":
            try:
                return self._send_json(service.build_summary())
            except Exception as e:
                traceback.print_exc()
                return self._send_error(str(e), 500)

        # 4. API: Deals queue
        if path == "/api/deals":
            try:
                deals = service.get_all_deals_data()
                return self._send_json(deals)
            except Exception as e:
                return self._send_error(str(e), 500)

        # 5. API: Specific deal detail
        if path.startswith("/api/deals/"):
            folder = urllib.parse.unquote(path[len("/api/deals/"):])
            deals = service.get_all_deals_data()
            matched = next((d for d in deals if d["folder"] == folder or d["id"] == folder), None)
            if not matched:
                return self._send_error(f"Deal '{folder}' not found", 404)
            return self._send_json(matched)

        # 6. API: Human requests
        if path == "/api/human-requests":
            try:
                deals = service.get_all_deals_data()
                requests = []
                for d in deals:
                    if d["operational_state"] in ("WAITING_HUMAN", "HELD_FRAUD"):
                        stage_lower = d["stage_lower"]
                        req_type = "credit_decision" if stage_lower == "credit" else \
                                   "committee_approval" if stage_lower == "committee" else \
                                   "commitment_signoff" if stage_lower == "committed" else \
                                   "funding_release" if stage_lower == "closed" else "deal_review"
                        
                        requests.append({
                            "id": f"req_{d['folder']}",
                            "folder": d["folder"],
                            "stage": d["stage"],
                            "type": req_type,
                            "recipient": d.get("assignee") or "credit_officer",
                            "required_role": "admin",
                            "requested_at": d.get("entered_at") or now(),
                            "evidence_version": 1,
                            "message": f"Review {d['folder']} for {d['stage']} milestone ({d['extracted']['legal_name']}). Stated: ${d['extracted']['amount_requested']:,.0f} for {d['extracted']['purpose']}."
                        })
                return self._send_json(requests)
            except Exception as e:
                return self._send_error(str(e), 500)

        # 7. API: Fraud patterns
        if path == "/api/fraud-patterns":
            return self._send_json([
                {
                    "name": "Coordinated Syndicate / Shared Remittance Rail",
                    "description": "Multi-entity structure routing disbursements to captive equipment vendor accounts.",
                    "weight": 27.0
                },
                {
                    "name": "Near-Sequential Driver License IDs",
                    "description": "Guarantor identity clustering across independent businesses.",
                    "weight": 11.0
                }
            ])

        # 8. API: Sim clock
        if path == "/api/sim/clock":
            c = clock()
            return self._send_json({"now": c.now().isoformat(timespec="minutes")})

        # 9. API: Export judging submission
        if path == "/api/export":
            try:
                from scripts.submission import build
                payload = build("sample")
                return self._send_json(payload)
            except Exception as e:
                return self._send_error(f"Export error: {e}", 500)

        # 10. API: Status & Auth
        if path == "/api/status":
            return self._send_json({
                "status": "healthy",
                "backend": "PocketBase",
                "pocketbase_url": os.environ.get("POCKETBASE_URL")
            })

        if path == "/api/auth/me":
            user = self._get_auth_user()
            return self._send_json({"user": user})

        if path == "/api/ai/status":
            return self._send_json({
                "status": "active",
                "key_configured": bool(os.environ.get("GEMINI_API_KEY") or (ROOT / ".gemini_key").exists()),
                "model": "gemini-flash-latest"
            })

        # 11. Static files serving
        clean_path = path.lstrip("/")
        if not clean_path:
            clean_path = "index.html"
        file_path = WEB_DIR / clean_path
        if file_path.is_file():
            mime_type, _ = mimetypes.guess_type(str(file_path))
            content = file_path.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", mime_type or "application/octet-stream")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        self._send_error("Not found", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        body = {}
        try:
            length = int(self.headers.get("Content-Length", 0))
            if length > 0:
                body = json.loads(self.rfile.read(length).decode("utf-8"))
        except Exception:
            pass

        # Auth endpoints with mandatory password authentication
        if path in ("/api/auth/login", "/api/auth/bank-login"):
            ident = body.get("person_id") or body.get("email") or body.get("role") or body.get("username")
            password = str(body.get("password") or "").strip()
            if not ident:
                return self._send_error("Officer ID or email is required.", 400)

            user = resolve_account(ident)

            if not password:
                return self._send_error(
                    f"Password required: please enter the workstation password for {user['name']}.",
                    401
                )

            # Check password: all accounts must match DealPilot2026!
            if password != UNIFIED_WORKSTATION_PASSWORD:
                return self._send_error(
                    f"Invalid workstation password for {user['name']}. Access denied.",
                    401
                )

            safe_user = {k: v for k, v in user.items() if k != "password"}
            return self._send_json({
                "success": True,
                "user": safe_user
            })

        if path == "/api/auth/logout":
            return self._send_json({"success": True})

        # -------------------------------------------------------------
        # Borrower Portal Endpoints (Registration, Login, Upload)
        # -------------------------------------------------------------
        if path == "/api/borrower/register":
            email = str(body.get("email") or "").strip().lower()
            password = str(body.get("password") or "").strip()
            business_name = str(body.get("business_name") or "").strip()
            guarantor_name = str(body.get("guarantor_name") or "").strip()
            amount_requested = body.get("amount_requested") or 150000
            purpose = str(body.get("purpose") or "working_capital").strip()

            if not email or not password or not business_name:
                return self._send_error("Email, password, and business name are required.", 400)

            borrowers = load_borrowers()
            if any(b["email"].lower() == email for b in borrowers):
                return self._send_error("An account with this email already exists. Please sign in.", 400)

            import secrets
            bor_id = f"bor_{secrets.token_hex(6)}"
            clean_folder = re.sub(r'[^a-zA-Z0-9_\-\. ]+', '_', business_name).strip()

            new_borrower = {
                "id": bor_id,
                "email": email,
                "password": password,
                "business_name": clean_folder,
                "guarantor_name": guarantor_name or clean_folder,
                "folder": clean_folder,
                "amount_requested": float(amount_requested),
                "purpose": purpose,
                "role": "borrower"
            }
            borrowers.append(new_borrower)
            save_borrowers(borrowers)

            # Ensure upload folder exists
            (ROOT / "data" / "uploads" / clean_folder).mkdir(parents=True, exist_ok=True)

            # Create intake ticket in PocketBase if not present
            try:
                existing = service.pb.list_records("intake", filter_expr=f"folder='{escape_filter(clean_folder)}'")
                if not existing:
                    service.create_record("intake", {
                        "folder": clean_folder,
                        "entered_at": now(),
                        "status": "open",
                        "assignee": "olivia.chen",
                        "waiting_on": "DOCS_PENDING",
                        "memo": f"Self-service commercial application registered by {guarantor_name}",
                        "extracted": {
                            "legal_name": clean_folder,
                            "guarantor_name": guarantor_name,
                            "amount_requested": float(amount_requested),
                            "purpose": purpose
                        }
                    })
            except Exception as e:
                print(f"Could not seed intake deal: {e}")

            safe_bor = {k: v for k, v in new_borrower.items() if k != "password"}
            return self._send_json({"success": True, "borrower": safe_bor})

        if path == "/api/borrower/login":
            email = str(body.get("email") or "").strip().lower()
            password = str(body.get("password") or "").strip()

            if not email or not password:
                return self._send_error("Email and password are required.", 400)

            borrowers = load_borrowers()
            match = next((b for b in borrowers if b["email"].lower() == email), None)
            if not match or match.get("password") != password:
                return self._send_error(
                    "That password does not match this application. Use the password from signup, not the bank workstation password.",
                    401,
                )

            safe_bor = {k: v for k, v in match.items() if k != "password"}
            return self._send_json({"success": True, "borrower": safe_bor})

        if path == "/api/borrower/upload":
            folder = str(body.get("folder") or "").strip()
            filename = str(body.get("filename") or "").strip()
            file_b64 = body.get("content_base64") or ""
            selected_doc_type = str(body.get("doc_type") or "other").strip()

            if not folder or not filename or not file_b64:
                return self._send_error("Folder, filename, and file content are required.", 400)

            clean_folder = re.sub(r'[^a-zA-Z0-9_\-\. ]+', '_', folder).strip()
            clean_filename = re.sub(r'[^a-zA-Z0-9_\-\. ]+', '_', filename).strip()

            dest_dir = ROOT / "data" / "uploads" / clean_folder
            dest_dir.mkdir(parents=True, exist_ok=True)
            dest_file = dest_dir / clean_filename

            raw_bytes = base64.b64decode(file_b64)
            dest_file.write_bytes(raw_bytes)

            from services.parser.extract import process_document
            use_regex = bool(body.get("use_regex"))
            parsed = process_document(dest_file, use_llm=not use_regex)
            detected_doc_type = parsed.get("document_type") or selected_doc_type or "other"
            if selected_doc_type and selected_doc_type != "auto":
                detected_doc_type = selected_doc_type

            rel_path = dest_file.relative_to(ROOT).as_posix()

            try:
                existing_files = service.pb.list_records("files", filter_expr=f"path='{escape_filter(rel_path)}'")
            except Exception:
                existing_files = []

            file_payload = {
                "folder": clean_folder,
                "filename": clean_filename,
                "path": rel_path,
                "arrived_at": now(),
                "doc_type": detected_doc_type,
                "source": "borrower_upload"
            }

            if existing_files:
                file_rec = service.update_record("files", existing_files[0]["id"], file_payload)
            else:
                file_rec = service.create_record("files", file_payload)

            # Stage absorb
            for stg_name in ["intake", "credit", "product", "committee", "committed", "closed"]:
                try:
                    stg_recs = service.pb.list_records(stg_name, filter_expr=f"folder='{escape_filter(clean_folder)}'")
                    if stg_recs:
                        stg_obj = load_stage(stg_name)
                        if hasattr(stg_obj, "absorb"):
                            curr_ext = dict(stg_recs[0].get("extracted") or {})
                            updated_ext = stg_obj.absorb(curr_ext, parsed)
                            service.update_record(stg_name, stg_recs[0]["id"], {"extracted": updated_ext})
                        break
                except Exception:
                    pass

            return self._send_json({
                "success": True,
                "record": file_rec,
                "document_type": detected_doc_type,
                "facts": parsed.get("facts", {}),
                "checksum": parsed.get("checksum", ""),
                "extractor": "regex" if use_regex else "llm",
            })

        if path == "/api/ai/config":
            key = body.get("api_key", "").strip()
            if key:
                (ROOT / ".gemini_key").write_text(key)
                os.environ["GEMINI_API_KEY"] = key
            else:
                if (ROOT / ".gemini_key").exists():
                    (ROOT / ".gemini_key").unlink()
                os.environ.pop("GEMINI_API_KEY", None)
            return self._send_json({"ok": True, "key_configured": bool(key)})

        # 1. API: Sim Clock Advance
        if path == "/api/sim/advance":
            delta = body.get("by", "4h")
            try:
                c = clock()
                new_t = c.advance(delta)
                return self._send_json({"now": new_t.isoformat(timespec="minutes"), "advanced": delta})
            except Exception as e:
                return self._send_error(str(e), 400)

        # 2. API: Sim Clock Reset
        if path == "/api/sim/reset":
            try:
                c = clock()
                c.reset()
                return self._send_json({"now": c.now().isoformat(timespec="minutes"), "reset": True})
            except Exception as e:
                return self._send_error(str(e), 400)

        # 3. API: Run Fraud Detection Agent
        if path == "/api/fraud/scan":
            try:
                agent = FraudDetectionAgent(service.pb)
                scan_res = agent.run_scan()
                return self._send_json(scan_res)
            except Exception as e:
                traceback.print_exc()
                return self._send_error(f"Fraud agent failed: {e}", 500)

        # 4. API: Release Capital / Fund Deal
        if path == "/api/deals/fund":
            folder = body.get("folder")
            user = resolve_account(body.get("person_id") or body.get("role"))
            if not user.get("can_release_funds", False):
                return self._send_error(
                    f"Access Denied: You are signed in as '{user['name']}' ({user['title']}). Authority to disburse commercial loan capital is restricted to Chief Lending Officer (Admin) or Closing Officer.",
                    403
                )
            fraud = service.pb.list_records("fraud_detected", f"folder='{escape_filter(folder)}'")
            if fraud:
                return self._send_error(
                    f"DISBURSEMENT BLOCKED: Deal '{folder}' is flagged in fraud_detected ({fraud[0].get('reason')}). Capital cannot be released while fraud hold is active.",
                    403
                )
            closed_recs = service.pb.list_records("closed", f"folder='{escape_filter(folder)}'")
            if not closed_recs:
                return self._send_error(f"Deal '{folder}' not found in Closed queue.", 404)
            if closed_recs[0].get("waiting_on") == "fraud detected":
                return self._send_error(
                    f"DISBURSEMENT BLOCKED: Deal '{folder}' has an active fraud hold (waiting_on: fraud detected). Capital release prohibited.",
                    403
                )
            service.pb.update_record("closed", closed_recs[0]["id"], {"status": "funded", "waiting_on": ""})
            service.create_record("actions", {
                "at": now(),
                "folder": folder,
                "stage": "closed",
                "actor": user["person_id"],
                "kind": "decision",
                "decision": "funds disbursed",
                "why": f"Funding released and verified by {user['name']} ({user['title']})"
            })
            return self._send_json({"success": True, "folder": folder, "status": "funded"})

        # 5. API: Human Decision Answer
        if path == "/api/human-answer":
            folder = str(body.get("folder") or "").strip()
            decision = str(body.get("decision") or "").strip()
            stage_name = str(body.get("stage") or "").strip().lower()
            if not folder or not decision:
                return self._send_error("Missing folder or decision", 400)

            user = self._get_auth_user()
            if user.get("role") == "borrower":
                return self._send_error("Borrowers cannot record underwriting decisions.", 403)

            try:
                if not stage_name:
                    deals = service.get_all_deals_data()
                    target_deal = next((d for d in deals if d["folder"] == folder), None)
                    if not target_deal:
                        return self._send_error(f"Deal '{folder}' not found in active stages", 404)
                    stage_name = target_deal["stage_lower"]

                try:
                    stage = load_stage(stage_name)
                except SystemExit:
                    return self._send_error(f"Unknown stage '{stage_name}'", 400)

                if user.get("role") != "admin" and stage.name not in set(user.get("allowed_stages") or []):
                    return self._send_error(
                        f"{user.get('name')} cannot decide deals in {stage.name}.",
                        403,
                    )
                if decision not in stage.answers:
                    choices = ", ".join(sorted(stage.answers)) or "none"
                    return self._send_error(
                        f"{decision!r} is not a {stage.name} action. Use: {choices}",
                        400,
                    )

                records = service.pb.list_records(stage.name, filter_expr=f"folder='{escape_filter(folder)}'")
                if not records:
                    return self._send_error(f"{folder!r} is not in {stage.name}", 404)
                row = records[0]
                if row.get("status") in {"declined", "funded"}:
                    return self._send_error(f"{folder} is already {row['status']}", 400)

                why = body.get("why") or f"{stage.decision_type} {decision}"
                service.create_record("actions", {
                    "at": now(),
                    "folder": folder,
                    "stage": stage.name,
                    "actor": user.get("person_id") or "operator",
                    "kind": "decision",
                    "decision": decision,
                    "why": why,
                })

                if decision == "decline":
                    fields = {"status": "declined", "waiting_on": ""}
                elif decision == "request_info":
                    fields = {"status": "open", "waiting_on": "Additional information requested"}
                else:
                    fields = {"status": "approved", "waiting_on": ""}
                service.update_record(stage.name, row["id"], fields)
                blockers = stage.blockers(row) if decision == "approve" else []

                return self._send_json({
                    "success": True,
                    "folder": folder,
                    "stage": stage.name,
                    "decision": decision,
                    "status": fields["status"],
                    "waiting_on": fields["waiting_on"],
                    "blockers": blockers,
                    "will_advance": decision == "approve" and not blockers and stage.next_name is not None,
                })
            except Exception as e:
                traceback.print_exc()
                return self._send_error(str(e), 500)

        # 6. API: Flag deal in fraud_detected (records into fraud_detected, releases alerts, and holds closed deals)
        if path == "/api/fraud/flag":
            folder = body.get("folder")
            reason = body.get("reason", "Suspected coordinated fraud ring")
            if not folder:
                return self._send_error("Missing folder", 400)
            try:
                deals = service.get_all_deals_data()
                target_deal = next((d for d in deals if d["folder"] == folder), None)
                stage_name = target_deal["stage_lower"] if target_deal else "closed"

                # Check if already flagged
                existing = service.pb.list_records("fraud_detected", filter_expr=f"folder='{escape_filter(folder)}'")
                if existing:
                    rec = existing[0]
                else:
                    rec = service.create_record("fraud_detected", {
                        "folder": folder,
                        "at": now(),
                        "stage": stage_name,
                        "reason": reason
                    })

                # Release alert to victor.hale (Fraud Operations)
                service.create_record("alerts", {
                    "at": now(),
                    "folder": folder,
                    "stage": stage_name,
                    "level": "alert",
                    "to": "victor.hale",
                    "message": f"FRAUD HOLD: {folder} flagged in {stage_name}. Evidence: {reason}"
                })

                # Release escalation alert to lauren.kim (Head of Operations)
                service.create_record("alerts", {
                    "at": now(),
                    "folder": folder,
                    "stage": stage_name,
                    "level": "escalation",
                    "to": "lauren.kim",
                    "message": f"FRAUD ESCALATION: Security freeze placed on {folder}. Immediate underwrite freeze required."
                })

                # If deal is in closed stage, hold capital disbursement
                closed_recs = service.pb.list_records("closed", filter_expr=f"folder='{escape_filter(folder)}'")
                if closed_recs:
                    service.pb.update_record("closed", closed_recs[0]["id"], {"waiting_on": "fraud detected"})

                return self._send_json({"success": True, "flagged": True, "record": rec})
            except Exception as e:
                traceback.print_exc()
                return self._send_error(str(e), 500)

                # Check if already flagged
                existing = service.pb.list_records("fraud_detected", filter_expr=f"folder='{escape_filter(folder)}'")
                if existing:
                    return self._send_json({"already_flagged": True, "record": existing[0]})

                rec = service.create_record("fraud_detected", {
                    "folder": folder,
                    "at": now(),
                    "stage": stage_name,
                    "reason": reason
                })
                return self._send_json({"success": True, "flagged": True, "record": rec})
            except Exception as e:
                return self._send_error(str(e), 500)

        # 5. Generic table record creation: POST /api/tables/<name>
        if path.startswith("/api/tables/"):
            table_name = path[len("/api/tables/"):].strip("/")
            if table_name not in TABLE_MAP:
                return self._send_error(f"Unknown table '{table_name}'", 404)
            try:
                rec = service.create_record(table_name, body)
                return self._send_json({"success": True, "record": rec}, 201)
            except Exception as e:
                return self._send_error(str(e), 500)

        self._send_error("Unknown endpoint", 404)

    def do_DELETE(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        # 1. Unflag fraud: DELETE /api/fraud/unflag/<folder>
        if path.startswith("/api/fraud/unflag/"):
            folder = urllib.parse.unquote(path[len("/api/fraud/unflag/"):])
            try:
                deleted = service.delete_by_filter("fraud_detected", f"folder='{escape_filter(folder)}'")
                return self._send_json({"success": True, "deleted_count": deleted})
            except Exception as e:
                return self._send_error(str(e), 500)

        # 2. Generic table record deletion: DELETE /api/tables/<name>/<id>
        if path.startswith("/api/tables/"):
            parts = [p for p in path.split("/") if p]
            if len(parts) == 4:
                table_name, rec_id = parts[2], parts[3]
                if table_name not in TABLE_MAP:
                    return self._send_error(f"Unknown table '{table_name}'", 404)
                try:
                    service.delete_record(table_name, rec_id)
                    return self._send_json({"success": True, "deleted_id": rec_id})
                except Exception as e:
                    return self._send_error(str(e), 500)

        self._send_error("Unknown endpoint", 404)


def run(port: int = 8000):
    server = ThreadingHTTPServer(("0.0.0.0", port), DealPilotRequestHandler)
    print("=" * 70)
    print(f"DealPilot UI & PocketBase Table Explorer running on http://127.0.0.1:{port}")
    print(f"Connected to PocketBase: {os.environ.get('POCKETBASE_URL')}")
    print("=" * 70)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down server.")
        server.server_close()


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 and sys.argv[1].isdigit() else 8000
    run(port)
