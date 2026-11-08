import unittest

from dealpilot import service


class DealPilotPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service.reset(clear_portal=True)
        result = service.advance_clock("4d")
        assert result["processed"] == 173

    def test_expected_portfolio_and_fraud_ring(self):
        summary = service.summary()
        self.assertEqual(summary["total_deals"], 13)
        flagged = {deal["folder"] for deal in summary["deals"] if deal["fraud"]["flagged"]}
        self.assertEqual(
            flagged,
            {"Pecan Grove Catering", "Ridgeway Freight Logistics", "Summit Peak Flooring"},
        )
        pecan = service.get_deal("Pecan Grove Catering")
        self.assertEqual(set(pecan["fraud"]["linked_deals"]), {
            "Ridgeway Freight Logistics", "Summit Peak Flooring", "H-2608-002", "H-2609-008"
        })

    def test_missing_and_inconsistent_evidence_blocks_progress(self):
        loopwise = service.get_deal("tyler loopwise loan stuff")
        self.assertIn("MISSING_DOC:bank_statement_2026-06", loopwise["issues"])
        self.assertEqual(loopwise["operational_state"], "WAITING_DOCUMENTS")
        westfield = service.get_deal("Westfield Print & Sign")
        self.assertIn("MISMATCH:ein", westfield["issues"])
        self.assertEqual(westfield["stage"], "Intake")

    def test_fraud_gate_holds_before_advancement(self):
        deal = service.get_deal("Pecan Grove Catering")
        service.evaluate_all()
        deal = service.get_deal(deal["id"])
        self.assertEqual(deal["stage"], "Intake")
        request = next(
            item for item in service.pending_requests()
            if item["deal_id"] == deal["id"] and item["type"] == "fraud_hold"
        )
        deal = service.answer_request(request["id"], "approve", "test.fraud", "fraud_ops")
        self.assertEqual(deal["operational_state"], "HELD_FRAUD")
        self.assertTrue(deal["fraud"]["held"])

    def test_clean_deal_can_reach_closed_only_through_human_gates(self):
        deal = service.get_deal("Garza Family Pharmacy")
        gates = [
            ("credit_decision", "credit_officer"),
            ("committee_approval", "committee"),
            ("commitment_signoff", "relationship_manager"),
            ("funding_release", "funding_officer"),
        ]
        for request_type, role in gates:
            request = next(
                item for item in service.pending_requests()
                if item["deal_id"] == deal["id"] and item["type"] == request_type
            )
            deal = service.answer_request(request["id"], "approve", "test.user", role)
        self.assertEqual(deal["stage"], "Closed")
        self.assertEqual(deal["operational_state"], "CLOSED")
        self.assertEqual([row["stage"] for row in deal["stage_log"]], [
            "Intake", "Credit", "Committee", "Product", "Committed", "Closed"
        ])


if __name__ == "__main__":
    unittest.main()
