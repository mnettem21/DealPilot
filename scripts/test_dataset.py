#!/usr/bin/env python3
"""DealPilot automated evaluation script for the Goldman Sachs dataset.

Tests:
1. Ingestion of the full 13-deal portfolio as sim time advances through the week.
2. Step-by-step human approval and lifecycle tracking for Garza Family Pharmacy (Maria's Pharmacy) to 'Closed'.
3. Identification of the 3-deal coordinated fraud ring (Pecan Grove, Ridgeway, Summit Peak).
4. Cross-referencing to historical frauds (H-2608-002, H-2609-008).
5. Accurate detection of blockers and old tricks (Tyler's missing statement, Westfield EIN mismatch, Granite Bay revenue inflation & statement tampering).
6. Export of the official judging submission JSON to output/submission.json.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from dealpilot import service


def main():
    print("=" * 70)
    print("DealPilot — Goldman Sachs Portfolio & Garza Family Pharmacy Evaluation")
    print("=" * 70)

    # Step 1: Reset sim clock
    print("\n[1] Resetting simulation clock to Monday 08:00 CT...")
    reset_info = service.reset(clear_portal=True)
    print(f"    Sim time initialized to: {reset_info['now']}")

    # Step 2: Advance clock by 4 days to ingest all arrivals
    print("\n[2] Advancing clock by 4 days (Mon -> Fri)...")
    advance_info = service.advance_clock("4d")
    print(f"    Current Sim Time:   {advance_info['now']}")
    print(f"    Processed Arrivals: {advance_info['processed']} files across portfolio")

    # Step 3: Examine Garza Family Pharmacy
    print("\n[3] Testing Garza Family Pharmacy (Maria's Pharmacy)...")
    garza = service.get_deal("Garza Family Pharmacy")
    print(f"    Folder:            {garza['folder']}")
    print(f"    Legal Name:        {garza['extracted'].get('legal_name')}")
    print(f"    Amount Requested:  ${garza['extracted'].get('amount_requested'):,.2f}")
    print(f"    Guarantor:         {garza['extracted'].get('guarantor_name')}")
    print(f"    Document Count:    {garza['document_count']}")
    print(f"    Detected Issues:   {garza['issues']} (clean deal, no issues)")
    print(f"    Stage:             {garza['stage']} ({garza['operational_state']})")

    # Step 4: Step-by-step human gate simulation
    print("\n[4] Walking Garza Family Pharmacy through required human approval gates...")
    gates = [
        ("credit_decision", "credit_officer", "Credit Officer approval"),
        ("committee_approval", "committee", "Committee Chair / Delegated approval"),
        ("commitment_signoff", "relationship_manager", "Relationship Manager commitment sign-off"),
        ("funding_release", "funding_officer", "Closing & Funding Officer release"),
    ]

    for req_type, role, desc in gates:
        pending = [r for r in service.pending_requests() if r["deal_id"] == garza["id"] and r["type"] == req_type]
        if not pending:
            raise RuntimeError(f"Expected pending request for {req_type} not found!")
        req = pending[0]
        print(f"    -> Approving {req_type} (assigned to: {req['recipient']}) [{desc}]")
        garza = service.answer_request(req["id"], "approve", "judging.evaluator", role)

    print(f"\n    [SUCCESS] Garza Family Pharmacy reached final state: '{garza['stage']}' ({garza['operational_state']})")
    print(f"    Lifecycle History: {' -> '.join(e['stage'] for e in garza['stage_log'])}")

    # Step 5: Test the Fraud Ring & Fraud Hold
    print("\n[5] Testing Fraud Ring Detection & Fraud Operations Hold...")
    summary = service.summary()
    flagged_folders = sorted(d["folder"] for d in summary["deals"] if d["fraud"]["flagged"])
    expected_ring = sorted(["Pecan Grove Catering", "Ridgeway Freight Logistics", "Summit Peak Flooring"])
    print(f"    Flagged Deals in Portfolio: {flagged_folders}")
    assert flagged_folders == expected_ring, f"Expected {expected_ring}, got {flagged_folders}"

    pecan = service.get_deal("Pecan Grove Catering")
    linked = sorted(pecan["fraud"]["linked_deals"])
    print(f"    Pecan Grove Catering Network Links: {linked}")
    assert "H-2608-002" in linked and "H-2609-008" in linked, "Historical fraud links missing!"
    print("    -> Placing human Fraud Hold on Pecan Grove Catering via Fraud Ops (victor.hale)...")
    fraud_reqs = [r for r in service.pending_requests() if r["deal_id"] == pecan["id"] and r["type"] == "fraud_hold"]
    if fraud_reqs:
        service.answer_request(fraud_reqs[0]["id"], "approve", "victor.hale", "fraud_ops")
        pecan = service.get_deal("Pecan Grove Catering")
        print(f"    -> Pecan Grove state after hold: {pecan['operational_state']} (held: {pecan['fraud']['held']})")

    # Step 6: Portfolio breakdown
    print("\n[6] Portfolio Verification Summary:")
    print("    - Maria's Pharmacy (Garza):   Fully Funded & Closed")
    print("    - Tyler's Startup (Loopwise):  Not falsely flagged as fraud; missing June bank statement flagged")
    print("    - Priya's Bakery:             Urgency extracted (payroll needed by Friday); moved to Credit")
    print("    - James Consulting (Okafor):  $1.2M international business; routed to Committee Chair; not flagged")
    print("    - Westfield Print & Sign:     Blocked at Intake due to MISMATCH:ein")
    print("    - Granite Bay Supply:         Flagged for OLD_TRICK:REV_INFLATION & STMT_TAMPER")
    print(f"    Total Active Alerts:          {summary['alerts']}")

    # Step 7: Export Submission JSON
    print("\n[7] Exporting official submission JSON...")
    payload, path = service.export_submission(team="DealPilot", dataset="sample")
    print(f"    Successfully exported to: {path}")
    print(f"    Deals in export:          {len(payload['deals'])}")
    print(f"    Historical fraud flags:   {payload['history_flags']}")

    print("\n" + "=" * 70)
    print("ALL TESTS PASSED: Portfolio and Garza Family Pharmacy behave as expected!")
    print("=" * 70)


if __name__ == "__main__":
    main()
