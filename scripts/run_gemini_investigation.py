#!/usr/bin/env python3
"""Run live Gemini AI cross-deal fraud investigation from the terminal.

Usage:
    export GEMINI_API_KEY="AIzaSy..."
    python3 scripts/run_gemini_investigation.py

    or:
    python3 scripts/run_gemini_investigation.py --api-key "AIzaSy..."
"""

import argparse
import json
import os
import sys
from pathlib import Path

# Add project root to sys.path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from dealpilot import gemini_advisor, service


def main():
    parser = argparse.ArgumentParser(description="Run Gemini AI Cross-Deal Fraud Investigation")
    parser.add_argument("--api-key", help="Gemini API Key (or set GEMINI_API_KEY env var)")
    parser.add_argument("--deal", default="Pecan Grove Catering", help="Specific deal folder to investigate")
    parser.add_argument("--question", default="Why was this application placed on fraud hold and what links exist?", help="Underwriter question to ask Gemini")
    args = parser.parse_args()

    if args.api_key:
        gemini_advisor.set_api_key(args.api_key)

    api_key = gemini_advisor.get_api_key()
    print("=" * 72)
    print("DealPilot — Gemini AI Cross-Deal Fraud Forensic Terminal")
    print("=" * 72)

    if api_key:
        masked_key = api_key[:6] + "..." + api_key[-4:] if len(api_key) > 10 else "***"
        print(f"[API Status] Using configured Gemini Key: {masked_key}")
    else:
        print("[API Status] No GEMINI_API_KEY detected in env or arguments.")
        print("             Running with DealPilot Built-in Forensic Intelligence Engine.")
        print("             (To use live Google API: export GEMINI_API_KEY='your-key-here')\n")

    print("[1] Loading portfolio and computing cross-deal graph links...")
    service.reset(clear_portal=True)
    service.advance_clock("4d")
    service.evaluate_all()

    deals = service.get_deals()
    flagged = [d for d in deals if d.get("fraud", {}).get("flagged")]
    print(f"    Total Deals Ingested: {len(deals)}")
    print(f"    Flagged Ring Deals:   {[d['folder'] for d in flagged]}")

    print("\n[2] Executing Multi-Deal Syndicate Investigation with Gemini...")
    ring_result = gemini_advisor.investigate_fraud_ring(flagged, deals)

    print("\n" + "-" * 72)
    print(f"VERDICT:   {ring_result.get('verdict')}")
    print(f"CONFIDENCE: {int(ring_result.get('confidence_score', 0) * 100)}%")
    print(f"PROVIDER:  {ring_result.get('ai_provider')}")
    print("-" * 72)

    print("\n[EXECUTIVE SUMMARY]")
    print(ring_result.get("executive_summary", "N/A"))

    print("\n[STRUCTURING & COMMITTEE THRESHOLD EVASION]")
    print(ring_result.get("structuring_analysis", "N/A"))

    print("\n[SYNDICATE MODUS OPERANDI]")
    print(ring_result.get("modus_operandi", "N/A"))

    print("\n[HISTORICAL RECIDIVISM]")
    print(ring_result.get("historical_recidivism", "N/A"))

    print("\n[FINCEN SUSPICIOUS ACTIVITY REPORT (SAR) NARRATIVE]")
    print(ring_result.get("sar_narrative", "N/A"))

    print("\n[RECOMMENDED ACTIONS FOR FRAUD OPS]")
    for i, action in enumerate(ring_result.get("recommended_actions", []), 1):
        print(f"  {i}. {action}")

    print("\n" + "=" * 72)
    print(f"[3] Testing Interactive Underwriter AI Q&A on: '{args.deal}'")
    print(f"    Question: \"{args.question}\"")
    print("=" * 72)

    target_deal = next((d for d in deals if d["folder"].lower() == args.deal.lower()), None)
    if not target_deal:
        print(f"Deal '{args.deal}' not found in portfolio.")
        return

    deal_ai = gemini_advisor.investigate_deal_with_gemini(target_deal, args.question)
    print(f"\n[AI Underwriter Response — {deal_ai.get('ai_provider')}]:\n")
    print(deal_ai.get("answer"))
    print("\n" + "=" * 72)
    print("Investigation successfully completed!")
    print("=" * 72)


if __name__ == "__main__":
    main()

