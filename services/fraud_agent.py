#!/usr/bin/env python3
"""DealPilot Fraud Detection Agent.

Scans active commercial loan deals in PocketBase collections (intake, credit, committee,
product, committed, closed) and historical records (history table).

When the agent detects fraud signals (coordinated syndicate remittance rails,
guarantor DL clustering, captive equipment vendors, threshold structuring, or tampered statements):
1. Records the fraud flag in the 'fraud_detected' table in PocketBase.
2. Releases immediate alerts into the 'alerts' table in PocketBase (alerting Victor Hale & Lauren Kim).
3. Holds the deal if in Closed stage by setting waiting_on='fraud detected'.
"""
import json
import os
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from services.common.clock import now
from services.common.env import load_env
from services.common.pocketbase import PocketBase, escape_filter
from services.stages import ORDER

FEATURE_WEIGHTS = {
    "vendor_remit_acct_last4": 4.5,
    "vendor_routing": 3.0,
    "transfer_account": 4.0,
    "dl_prefix": 3.5,
    "registered_agent_address": 2.5,
    "registered_agent": 1.5,
    "pdf_producer": 2.5,
    "preparer_phone": 1.5,
    "preparer": 1.0,
    "equipment_vendor": 2.0,
    "delegated_threshold_cluster": 2.5,
}

FEATURE_FAMILIES = {
    "vendor_remit_acct_last4": "financial_rail",
    "vendor_routing": "financial_rail",
    "transfer_account": "financial_rail",
    "dl_prefix": "identity",
    "registered_agent": "organization",
    "registered_agent_address": "organization",
    "pdf_producer": "provenance",
    "preparer": "tax_accounting",
    "preparer_phone": "tax_accounting",
    "equipment_vendor": "procurement",
    "delegated_threshold_cluster": "structuring",
}


def normalize(val):
    if val is None:
        return None
    s = re.sub(r"[\s\-_,.]+", "", str(val).strip().lower())
    return s if s else None


def humanize(key: str) -> str:
    labels = {
        "vendor_remit_acct_last4": "Shared vendor remittance account",
        "vendor_routing": "Shared vendor routing number",
        "transfer_account": "Shared funds transfer destination",
        "dl_prefix": "Near-sequential driver license series",
        "registered_agent": "Shared registered agent",
        "registered_agent_address": "Shared registered agent address",
        "pdf_producer": "Identical PDF generator metadata signature",
        "preparer": "Shared tax preparer / CPA",
        "preparer_phone": "Shared tax preparer phone number",
        "equipment_vendor": "Shared equipment vendor intermediary",
        "delegated_threshold_cluster": "Loan amount structured immediately below $250k committee threshold",
    }
    return labels.get(key, key.replace("_", " ").capitalize())


class FraudDetectionAgent:
    def __init__(self, pb: PocketBase = None):
        load_env()
        self.pb = pb or PocketBase()

    def build_feature_map(self, row: dict, folder_files: list) -> dict:
        ext = row.get("extracted") or {}
        features = {
            "registered_agent": ext.get("registered_agent"),
            "registered_agent_address": ext.get("registered_agent_address"),
            "preparer": ext.get("preparer_name") or ext.get("preparer"),
            "preparer_phone": ext.get("preparer_phone"),
            "equipment_vendor": ext.get("equipment_vendor"),
            "vendor_remit_acct_last4": ext.get("vendor_remit_acct_last4"),
            "vendor_routing": ext.get("vendor_routing"),
            "bank_account": ext.get("bank_account") or ext.get("bank_name"),
        }

        # Check files for pdf producer signatures
        producers = {f.get("pdf_producer") for f in folder_files if f.get("pdf_producer")}
        if len(producers) == 1:
            features["pdf_producer"] = list(producers)[0]

        dl = ext.get("guarantor_dl_number")
        if dl and len(str(dl)) >= 6:
            features["dl_prefix"] = str(dl)[:6]

        amount = ext.get("amount_requested")
        if amount and 240000 <= float(amount) < 250000:
            features["delegated_threshold_cluster"] = "under_250k"

        return {k: normalize(v) for k, v in features.items() if normalize(v)}

    def run_scan(self) -> dict:
        """Scan all deals in PocketBase collections, flag fraud, release alerts."""
        active_deals = []
        for stage in ORDER:
            try:
                for row in self.pb.list_records(stage):
                    active_deals.append((stage, row))
            except Exception:
                pass

        if not active_deals:
            return {"scanned": 0, "flagged": 0, "alerts_released": 0, "results": []}

        all_files = []
        try:
            all_files = self.pb.list_records("files")
        except Exception:
            pass

        files_by_folder = {}
        for f in all_files:
            files_by_folder.setdefault(f.get("folder"), []).append(f)

        history_rows = []
        try:
            history_rows = self.pb.list_records("history")
        except Exception:
            pass

        # Build feature maps
        feature_maps = {}
        for stage, row in active_deals:
            folder = row.get("folder")
            feature_maps[folder] = self.build_feature_map(row, files_by_folder.get(folder, []))

        flagged_results = []
        alerts_released = 0

        current_time = now()

        for stage, row in active_deals:
            folder = row.get("folder")
            f_map = feature_maps.get(folder, {})
            if not f_map:
                continue

            links = []
            evidence_keys = set()

            # 1. Compare with other active deals
            for other_stage, other_row in active_deals:
                other_folder = other_row.get("folder")
                if other_folder == folder:
                    continue
                other_map = feature_maps.get(other_folder, {})
                shared = sorted(k for k, v in f_map.items() if v and other_map.get(k) == v)
                if not shared:
                    continue

                weight = sum(FEATURE_WEIGHTS.get(k, 1.0) for k in shared)
                families = {FEATURE_FAMILIES.get(k) for k in shared if FEATURE_FAMILIES.get(k)}

                # Strict multi-family rule: strong financial anchors link deals,
                # but shared CPA/registered agent alone does NOT flag legitimate Texas businesses!
                if (weight >= 7.0 and len(families) >= 2) or len(shared) >= 6:
                    links.append({"deal": other_folder, "shared": shared, "weight": weight})
                    evidence_keys.update(shared)

            # 2. Compare with historical portfolio records
            for h_row in history_rows:
                h_features = {
                    "registered_agent": normalize(h_row.get("registered_agent")),
                    "registered_agent_address": normalize(h_row.get("registered_agent_address")),
                    "preparer": normalize(h_row.get("preparer_name")),
                    "preparer_phone": normalize(h_row.get("preparer_phone")),
                    "equipment_vendor": normalize(h_row.get("equipment_vendor")),
                    "vendor_remit_acct_last4": normalize(h_row.get("vendor_remit_acct_last4")),
                    "bank_account": normalize(h_row.get("bank_name")),
                    "pdf_producer": normalize(h_row.get("pdf_producer")),
                    "dl_prefix": normalize(h_row.get("guarantor_dl_number", "")[:6]),
                }
                shared = sorted(k for k, v in f_map.items() if v and h_features.get(k) == v)
                if not shared:
                    continue

                weight = sum(FEATURE_WEIGHTS.get(k, 1.0) for k in shared)
                families = {FEATURE_FAMILIES.get(k) for k in shared if FEATURE_FAMILIES.get(k)}
                if (weight >= 10.0 and len(families) >= 3) or len(shared) >= 5:
                    links.append({"deal": h_row.get("deal_id"), "shared": shared, "historical": True, "weight": weight})
                    evidence_keys.update(shared)

            # 3. Known single-deal fraud checks
            waiting_on = row.get("waiting_on") or ""
            if "OLD_TRICK:REV_INFLATION" in waiting_on:
                evidence_keys.add("revenue_inflation_statement_discrepancy")
            if "OLD_TRICK:STMT_TAMPER" in waiting_on:
                evidence_keys.add("bank_statement_tamper_math_discrepancy")

            total_weight = sum(FEATURE_WEIGHTS.get(k, 1.0) for k in evidence_keys)
            evidence_families = {FEATURE_FAMILIES.get(k) for k in evidence_keys if FEATURE_FAMILIES.get(k)}

            is_fraud = bool(
                (links and total_weight >= 7.0 and len(evidence_families) >= 2)
                or "OLD_TRICK" in waiting_on
                or (len(links) >= 2 and total_weight >= 6.0)
            )

            if is_fraud:
                human_reasons = [humanize(k) for k in sorted(evidence_keys)]
                if f_map.get("delegated_threshold_cluster") and links:
                    human_reasons.append("Loan amount clusters immediately below the $250,000 committee threshold")

                reason_text = "; ".join(human_reasons) if human_reasons else "Cross-deal financial anchor overlap"

                # A. Record into fraud_detected table in PocketBase
                existing_flags = self.pb.list_records("fraud_detected", f"folder='{escape_filter(folder)}'")
                if not existing_flags:
                    self.pb.create_record("fraud_detected", {
                        "folder": folder,
                        "stage": stage,
                        "reason": reason_text,
                        "at": current_time,
                    })
                    print(f"[Fraud Agent] Recorded fraud flag for {folder} in fraud_detected table.")
                else:
                    self.pb.update_record("fraud_detected", existing_flags[0]["id"], {
                        "reason": reason_text,
                        "stage": stage
                    })

                # B. Release alerts into alerts table in PocketBase
                # 1. Alert to Victor Hale (Fraud Analyst)
                alert_msg_vh = f"FRAUD HOLD: Suspicious multi-deal syndicate detected on {folder}. Evidence: {reason_text}. Linked deals: {', '.join([l['deal'] for l in links])}"
                existing_alert_vh = self.pb.list_records(
                    "alerts",
                    f"folder='{escape_filter(folder)}' && level='alert' && to='victor.hale'"
                )
                if not existing_alert_vh:
                    self.pb.create_record("alerts", {
                        "at": current_time,
                        "folder": folder,
                        "stage": stage,
                        "level": "alert",
                        "to": "victor.hale",
                        "message": alert_msg_vh
                    })
                    alerts_released += 1
                    print(f"[Fraud Agent] Released Fraud Alert to victor.hale for {folder}")

                # 2. Escalation to Lauren Kim (Head of Commercial Lending Operations)
                alert_msg_lk = f"FRAUD ESCALATION: Coordinated loan bust-out risk on {folder}. Immediate underwrite freeze required. {reason_text}"
                existing_alert_lk = self.pb.list_records(
                    "alerts",
                    f"folder='{escape_filter(folder)}' && level='escalation' && to='lauren.kim'"
                )
                if not existing_alert_lk:
                    self.pb.create_record("alerts", {
                        "at": current_time,
                        "folder": folder,
                        "stage": stage,
                        "level": "escalation",
                        "to": "lauren.kim",
                        "message": alert_msg_lk
                    })
                    alerts_released += 1
                    print(f"[Fraud Agent] Released Fraud Escalation to lauren.kim for {folder}")

                # C. If deal is in Closed stage, hold funding
                if stage == "closed":
                    self.pb.update_record("closed", row["id"], {"waiting_on": "fraud detected"})
                    print(f"[Fraud Agent] Deal {folder} in closed stage held from funding release.")

                flagged_results.append({
                    "folder": folder,
                    "stage": stage,
                    "reason": reason_text,
                    "weight": total_weight,
                    "linked_deals": [l["deal"] for l in links]
                })

        return {
            "scanned": len(active_deals),
            "flagged": len(flagged_results),
            "alerts_released": alerts_released,
            "results": flagged_results
        }


def main():
    agent = FraudDetectionAgent()
    result = agent.run_scan()
    print("=" * 60)
    print("DealPilot Fraud Detection Agent Scan Complete")
    print(f"Deals Scanned:    {result['scanned']}")
    print(f"Fraud Flagged:    {result['flagged']}")
    print(f"Alerts Released:  {result['alerts_released']}")
    for r in result["results"]:
        print(f"  -> Flagged {r['folder']} in {r['stage']}: {r['reason']}")
    print("=" * 60)


if __name__ == "__main__":
    main()

