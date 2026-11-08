#!/usr/bin/env python3
"""DealPilot Custom Scenarios Generator and Verification Script.

Creates and evaluates two brand-new loan scenarios:
1. Scenario A (70-89 Middle Tier / Human Review):
   - Borrower: Carlos Mendez, Lone Star Roasters LLC
   - Request: $100,000 Working Capital Loan
   - Package: Clean business, but missing tax return and formal P&L statements.
   - Result: Approval-Readiness Score 84 (Tier: HUMAN_REVIEW).
   - Borrower portal shows ONLY: Score 84, Tier 'Human Review', suggested $50,000 facility.
   - Bank Workspace receives: Deal dossier + Reviewable Email Draft offering the 50% loan ($50,000)
     and explaining the 85-90% historical approval rate pathway for the full amount.

2. Scenario B (< 70 Low Tier / Decline Recommended):
   - Borrower: Sarah Jenkins, Apex Cloud Dynamics LLC
   - Request: $250,000 Expansion Loan
   - Package: Has an EIN mismatch between application and CP575 notice, plus stated revenue of
     $2.5M is not supported by bank deposits (REV_INFLATION), and missing tax return.
   - Result: Approval-Readiness Score 47 (Tier: DECLINE_RECOMMENDED).
   - Borrower portal shows ONLY: Score 47, Tier 'Decline Recommended', status 'Application status'.
   - Bank Workspace receives: Deal dossier + Reviewable Email Draft detailing specific areas to work on
     and the 30-day cure, 3-6 month cash flow, and 90-day reapplication roadmap.
"""

from __future__ import annotations

import base64
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dealpilot import auth, consumer, service, db
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas


def create_simple_pdf(path: Path, title: str, content_lines: list[str]):
    path.parent.mkdir(parents=True, exist_ok=True)
    c = canvas.Canvas(str(path), pagesize=letter)
    c.setTitle(title)
    c.setFont("Helvetica-Bold", 16)
    c.drawString(50, 740, title)
    c.setFont("Helvetica", 10)
    y = 700
    for line in content_lines:
        c.drawString(50, y, line)
        y -= 22
    c.save()


def main():
    print("=" * 75)
    print("DealPilot — Custom Scenario Testing: Middle Tier (70-89) & Low Tier (<70)")
    print("=" * 75)

    base_out = ROOT / "output" / "pdf" / "custom_scenarios"

    # =========================================================================
    # SCENARIO A: Lone Star Roasters LLC (Score 70-89)
    # =========================================================================
    print("\n[SCENARIO A] Creating 'Lone Star Roasters LLC' (Target Score: 70–89)...")
    dir_a = base_out / "scenario_a_lone_star_roasters"
    dir_a.mkdir(parents=True, exist_ok=True)

    # Generate documents
    create_simple_pdf(dir_a / "01_id.pdf", "Texas Driver License", [
        "Name", "Carlos Mendez",
        "Address", "742 Roaster Way, Austin, TX 78701",
        "Date of birth", "1985-04-18",
        "DL number", "32984102",
    ])
    create_simple_pdf(dir_a / "02_formation.pdf", "Certificate of Formation", [
        "Entity name", "Lone Star Roasters LLC",
        "Effective date", "2020-03-15",
        "Principal office", "742 Roaster Way, Austin, TX 78701",
        "Registered agent", "Capitol Agent Services LLC",
        "Registered office", "100 Congress Ave, Austin, TX 78701",
    ])
    create_simple_pdf(dir_a / "03_ein.pdf", "CP 575 Notice", [
        "Name of business", "Lone Star Roasters LLC",
        "Employer Identification Number", "74-9812401",
    ])
    # 6 bank statements with mathematically continuous balances
    balances = [
        ("2026-05", "$45,000.00", "$60,000.00", "$48,000.00"),
        ("2026-06", "$48,000.00", "$62,000.00", "$50,000.00"),
        ("2026-07", "$50,000.00", "$65,000.00", "$53,000.00"),
        ("2026-08", "$53,000.00", "$64,000.00", "$55,000.00"),
        ("2026-09", "$55,000.00", "$68,000.00", "$58,000.00"),
        ("2026-10", "$58,000.00", "$70,000.00", "$62,000.00"),
    ]
    for month, opening, deposits, closing in balances:
        create_simple_pdf(dir_a / f"bank_statement_{month}.pdf", "Business Checking Statement", [
            "Statement period", f"{month}-01 to {month}-28",
            "Opening balance", opening,
            "Total deposits & credits", deposits,
            "Closing balance", closing,
        ])

    # Note: Deliberately omitting tax return and financial statements!
    print("    -> Generated 9 supporting documents (omitted tax return & audited financials).")

    # Create user and application
    try:
        user_a, token_a = auth.signup(
            "carlos@lonestarroasters.example", "RoastersPass123!", "Carlos Mendez", "Lone Star Roasters LLC"
        )
    except ValueError:
        user_a, token_a = auth.login("carlos@lonestarroasters.example", "RoastersPass123!")
    app_a = consumer.create_application(user_a)
    form_a = {
        "legal_name": "Lone Star Roasters LLC",
        "dba": "Lone Star Coffee",
        "ein": "74-9812401",
        "formation_date": "2020-03-15",
        "business_address": "742 Roaster Way, Austin, TX 78701",
        "industry": "Coffee roasting and wholesale supply",
        "annual_revenue_stated": "720000",
        "bank_account": "First Austin Bank / acct 8812",
        "amount_requested": "100000",
        "product": "Working Capital Loan",
        "purpose": "Expand green coffee inventory and add packaging line.",
        "term_months": "36",
        "needed_by": "2026-11-25T15:00",
        "guarantor_name": "Carlos Mendez",
        "guarantor_dob": "1985-04-18",
        "guarantor_address": "742 Roaster Way, Austin, TX 78701",
        "guarantor_dl_number": "32984102",
        "purpose_category": "working_capital",
    }
    consumer.save_application(app_a["id"], user_a["id"], form_a, 4)

    for pdf in sorted(dir_a.glob("*.pdf")):
        b64 = base64.b64encode(pdf.read_bytes()).decode()
        consumer.upload_document(app_a["id"], user_a["id"], pdf.name, b64)

    submitted_a = consumer.submit_application(app_a["id"], user_a)
    score_a = submitted_a["score"]
    dec_a = submitted_a["decision"]

    print("\n[RESULT A - BORROWER PORTAL VIEW]")
    print(f"    Approval-Readiness Score: {score_a['approval_readiness']} / 100")
    print(f"    Tier:                     {score_a['tier']}")
    print(f"    Suggested Amount:         ${score_a['suggested_amount']:,.2f}")
    print(f"    Recommendation:           {dec_a['recommendation']}")
    print("    * Notice: Internal bank drafts and risk markers are NOT visible to the client.")

    print("\n[RESULT A - BANK EMPLOYEE WORKSPACE VIEW]")
    print("    * Generated Email Notice Draft for Review:")
    print("-" * 65)
    print(dec_a["email_draft"])
    print("-" * 65)

    assert score_a["approval_readiness"] >= 70 and score_a["approval_readiness"] < 90, "Score A out of range!"
    assert "$50,000.00" in dec_a["email_draft"], "Email A missing 50% suggested amount!"
    assert "85%–90%" in dec_a["email_draft"], "Email A missing full-amount approval rate!"

    # =========================================================================
    # SCENARIO B: Apex Cloud Dynamics LLC (Score < 70)
    # =========================================================================
    print("\n" + "=" * 75)
    print("[SCENARIO B] Creating 'Apex Cloud Dynamics LLC' (Target Score: < 70)...")
    dir_b = base_out / "scenario_b_apex_cloud"
    dir_b.mkdir(parents=True, exist_ok=True)

    create_simple_pdf(dir_b / "01_id.pdf", "Texas Driver License", [
        "Name", "Sarah Jenkins",
        "Address", "1200 Tech Blvd, Dallas, TX 75201",
        "Date of birth", "1992-09-10",
        "DL number", "51920394",
    ])
    create_simple_pdf(dir_b / "02_formation.pdf", "Certificate of Formation", [
        "Entity name", "Apex Cloud Dynamics LLC",
        "Effective date", "2024-08-01",
        "Principal office", "1200 Tech Blvd, Dallas, TX 75201",
        "Registered agent", "Texas Corporate Filers",
        "Registered office", "500 Main St, Dallas, TX 75201",
    ])
    # Deliberate EIN mismatch: Letter says 99-1234567, but application says 88-7654321!
    create_simple_pdf(dir_b / "03_ein.pdf", "CP 575 Notice", [
        "Name of business", "Apex Cloud Dynamics LLC",
        "Employer Identification Number", "99-1234567",
    ])
    # Bank statements show $20k deposits, but stated revenue is $2,500,000 (REV_INFLATION)!
    for m in range(5, 11):
        month = f"2026-{m:02d}"
        create_simple_pdf(dir_b / f"bank_statement_{month}.pdf", "Business Checking Statement", [
            "Statement period", f"{month}-01 to {month}-28",
            "Opening balance", "$12,000.00",
            "Total deposits & credits", "$20,000.00",
            "Closing balance", "$11,500.00",
        ])

    print("    -> Generated documents with planted mismatches and revenue inflation.")

    try:
        user_b, token_b = auth.signup(
            "sarah@apexcloud.example", "ApexPass123456!", "Sarah Jenkins", "Apex Cloud Dynamics LLC"
        )
    except ValueError:
        user_b, token_b = auth.login("sarah@apexcloud.example", "ApexPass123456!")
    app_b = consumer.create_application(user_b)
    form_b = {
        "legal_name": "Apex Cloud Dynamics LLC",
        "dba": "Apex Cloud",
        "ein": "88-7654321",  # Deliberate mismatch vs 99-1234567 in EIN letter
        "formation_date": "2024-08-01",
        "business_address": "1200 Tech Blvd, Dallas, TX 75201",
        "industry": "Cloud infrastructure consulting",
        "annual_revenue_stated": "2500000",  # Deliberate REV_INFLATION vs $240k deposits
        "bank_account": "Dallas Commerce Bank / acct 9901",
        "amount_requested": "250000",
        "product": "Working Capital Loan",
        "purpose": "Hiring sales team and expanding data center footprints.",
        "term_months": "60",
        "needed_by": "2026-11-30T15:00",
        "guarantor_name": "Sarah Jenkins",
        "guarantor_dob": "1992-09-10",
        "guarantor_address": "1200 Tech Blvd, Dallas, TX 75201",
        "guarantor_dl_number": "51920394",
        "purpose_category": "working_capital",
    }
    consumer.save_application(app_b["id"], user_b["id"], form_b, 4)

    for pdf in sorted(dir_b.glob("*.pdf")):
        b64 = base64.b64encode(pdf.read_bytes()).decode()
        consumer.upload_document(app_b["id"], user_b["id"], pdf.name, b64)

    submitted_b = consumer.submit_application(app_b["id"], user_b)
    score_b = submitted_b["score"]
    dec_b = submitted_b["decision"]

    print("\n[RESULT B - BORROWER PORTAL VIEW]")
    print(f"    Approval-Readiness Score: {score_b['approval_readiness']} / 100")
    print(f"    Tier:                     {score_b['tier']}")
    print(f"    Suggested Amount:         ${score_b['suggested_amount']:,.2f}")
    print(f"    Recommendation:           {dec_b['recommendation']}")
    print("    * Notice: The applicant sees only their score and review status.")

    print("\n[RESULT B - BANK EMPLOYEE WORKSPACE VIEW]")
    print("    * Generated Email Notice Draft for Review:")
    print("-" * 65)
    print(dec_b["email_draft"])
    print("-" * 65)

    assert score_b["approval_readiness"] < 70, f"Expected score < 70, got {score_b['approval_readiness']}"
    assert "30 Days" in dec_b["email_draft"], "Missing 30-day cure window!"
    assert "3–6 Months" in dec_b["email_draft"], "Missing 3-6 month operating timeline!"
    assert "90 Days" in dec_b["email_draft"], "Missing 90-day reapplication window!"

    print("\n" + "=" * 75)
    print("ALL TESTS PASSED: Both custom scenarios evaluated with 100% precision!")
    print("=" * 75)


if __name__ == "__main__":
    main()
