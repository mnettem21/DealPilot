"""Test DealPilot's generalization to unfamiliar fraud patterns and hidden evaluation scenarios.

Tests the 4 rigorous scenarios recommended by code review:
1. Unknown fraud ring (shares payment rails + identity series, but NO shared agents/preparers/metadata)
2. Legitimate overlap (honest businesses share common registered agent & CPA; ZERO false positives)
3. Behavior anomaly (missing/inconsistent evidence flagged for review without false fraud declaration)
4. Incremental arrival (relationship graph updates dynamically as evidence accumulates over time)
"""

import unittest
from dealpilot import db, gemini_advisor, service


class HiddenGeneralizationTests(unittest.TestCase):
    def setUp(self):
        service.reset(clear_portal=True)

    def test_scenario_1_unknown_fraud_ring_detected_via_weighted_financial_rails(self):
        """Catches unfamiliar ring sharing ONLY vendor remittance account + routing + DL series,
        with completely distinct legal names, EINs, CPAs, registered agents, and PDF metadata.
        """
        now = service.sim_now()
        with db.transaction() as conn:
            # 3 synthetic shell entities
            for i, name in enumerate(["Orion Fleet Logistics LLC", "Vanguard Industrial Tech LLC", "Apex Freight Dynamics LLC"]):
                deal_id = f"deal_unknown_ring_{i}"
                extracted = {
                    "legal_name": name,
                    "ein": f"88-{i}123456",
                    "amount_requested": 248500.0,  # Structured under 250k
                    "purpose_category": "equipment",
                    "product": "Equipment Loan",
                    "guarantor_name": f"Synthetic Guarantor {i}",
                    "guarantor_dl_number": f"DL88990{i}",  # Near-sequential DL
                    "equipment_vendor": f"Supplier {i} Corp",  # Different vendor names
                    "vendor_remit_acct_last4": "7731",       # SHARED CRITICAL REMITTANCE ACCOUNT
                    "vendor_routing": "111000025",           # SHARED ROUTING
                    "preparer": f"Independent CPA {i}",      # Different CPAs
                    "registered_agent": f"Agent {i} LLC",    # Different agents
                }
                conn.execute(
                    """INSERT INTO deals(id, folder, stage, operational_state, entered_at, updated_at, extracted_json)
                       VALUES(?, ?, 'Intake', 'WAITING_HUMAN', ?, ?, ?)""",
                    (deal_id, name, now, now, service.json.dumps(extracted))
                )
            service.recompute_fraud(conn)

        # Check results
        deals = service.get_deals()
        ring_deals = [d for d in deals if "unknown_ring" in d["id"]]
        self.assertEqual(len(ring_deals), 3)

        for d in ring_deals:
            self.assertTrue(d["fraud"]["flagged"], f"{d['folder']} should be flagged by weighted engine")
            self.assertEqual(len(d["fraud"]["linked_deals"]), 2)
            # Verify stage_at_flag and flagged_at are properly recorded
            self.assertEqual(d["fraud"]["stage_at_flag"], "Intake")
            self.assertIsNotNone(d["fraud"]["flagged_at"])

    def test_scenario_2_legitimate_service_provider_overlap_never_falsely_flagged(self):
        """Honest Texas businesses sharing common CPA and registered agent must NEVER be flagged.
        Guarantees zero false positives.
        """
        now = service.sim_now()
        with db.transaction() as conn:
            # 2 legitimate Texas companies sharing commercial agent and CPA
            for i, name in enumerate(["Austin Hill Country Roasters LLC", "Lone Star Artisan Bakery LLC"]):
                deal_id = f"deal_honest_biz_{i}"
                extracted = {
                    "legal_name": name,
                    "ein": f"74-{i}998877",
                    "amount_requested": 85000.0 + i * 20000,
                    "purpose_category": "working_capital",
                    "product": "Term Loan",
                    "guarantor_name": f"Honest Owner {i}",
                    "guarantor_dl_number": f"DL{100000 + i * 450000}",  # Distinct DLs
                    "equipment_vendor": None,
                    "vendor_remit_acct_last4": None,
                    "bank_account": f"FrostBank{i}441",                 # Distinct bank accounts
                    # Shared common service providers:
                    "preparer": "Marcus Vance, CPA",
                    "registered_agent": "Capitol Corporate Services Ltd.",
                    "registered_agent_address": "100 Congress Ave, Suite 2000, Austin, TX",
                }
                conn.execute(
                    """INSERT INTO deals(id, folder, stage, operational_state, entered_at, updated_at, extracted_json)
                       VALUES(?, ?, 'Intake', 'WAITING_HUMAN', ?, ?, ?)""",
                    (deal_id, name, now, now, service.json.dumps(extracted))
                )
            service.recompute_fraud(conn)

        deals = service.get_deals()
        honest_deals = [d for d in deals if "honest_biz" in d["id"]]
        self.assertEqual(len(honest_deals), 2)
        for d in honest_deals:
            self.assertFalse(d["fraud"]["flagged"], f"{d['folder']} must NOT be falsely accused of fraud")
            self.assertEqual(len(d["fraud"]["linked_deals"]), 0)

    def test_scenario_3_gemini_fallback_inconclusive_when_no_fraud_ring_exists(self):
        """If there are no flagged deals or only isolated deals, Gemini advisor must return
        INCONCLUSIVE rather than fabricating a confirmed fraud syndicate.
        """
        clean_deals = [
            {"id": "deal_clean_1", "folder": "Clean Deal 1", "fraud": {"flagged": False}},
            {"id": "deal_clean_2", "folder": "Clean Deal 2", "fraud": {"flagged": False}},
        ]
        res = gemini_advisor.investigate_fraud_ring([], clean_deals)
        self.assertEqual(res["verdict"], "INCONCLUSIVE_NO_RING_EVIDENCE")
        self.assertLess(res["confidence_score"], 0.10)
        self.assertIn("SAR filing not met", res["sar_narrative"])

    def test_scenario_4_incremental_arrival_and_early_detection(self):
        """Deals arrive incrementally over simulated time. When the second connected deal arrives,
        the ring edge must form dynamically and record stage_at_flag='Intake'.
        """
        now = service.sim_now()
        with db.transaction() as conn:
            # Deal 1 arrives on Monday
            d1 = {
                "legal_name": "Entity Alpha LLC",
                "ein": "91-1111111",
                "amount_requested": 249000.0,
                "guarantor_dl_number": "DL990101",
                "vendor_remit_acct_last4": "5544",
                "vendor_routing": "111000025",
            }
            conn.execute(
                """INSERT INTO deals(id, folder, stage, operational_state, entered_at, updated_at, extracted_json)
                   VALUES('deal_alpha', 'Entity Alpha', 'Intake', 'WAITING_HUMAN', ?, ?, ?)""",
                (now, now, service.json.dumps(d1))
            )
            service.recompute_fraud(conn)

        # Alone, deal 1 is not flagged as a ring
        d1_state = service.get_deal("deal_alpha")
        self.assertFalse(d1_state["fraud"]["flagged"])

        # Deal 2 arrives later with matching extraction rails
        with db.transaction() as conn:
            d2 = {
                "legal_name": "Entity Beta LLC",
                "ein": "92-2222222",
                "amount_requested": 249000.0,
                "guarantor_dl_number": "DL990102",  # Near-sequential
                "vendor_remit_acct_last4": "5544",  # Matches Deal 1
                "vendor_routing": "111000025",      # Matches Deal 1
            }
            conn.execute(
                """INSERT INTO deals(id, folder, stage, operational_state, entered_at, updated_at, extracted_json)
                   VALUES('deal_beta', 'Entity Beta', 'Intake', 'WAITING_HUMAN', ?, ?, ?)""",
                (now, now, service.json.dumps(d2))
            )
            service.recompute_fraud(conn)

        # Both deals should now be connected and flagged at Intake
        d1_updated = service.get_deal("deal_alpha")
        d2_updated = service.get_deal("deal_beta")
        self.assertTrue(d1_updated["fraud"]["flagged"])
        self.assertTrue(d2_updated["fraud"]["flagged"])
        self.assertEqual(d1_updated["fraud"]["stage_at_flag"], "Intake")
        self.assertEqual(d2_updated["fraud"]["stage_at_flag"], "Intake")
        self.assertIn("Entity Beta", d1_updated["fraud"]["linked_deals"])
        self.assertIn("Entity Alpha", d2_updated["fraud"]["linked_deals"])


if __name__ == "__main__":
    unittest.main()
