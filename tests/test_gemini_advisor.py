import unittest
from dealpilot import db, gemini_advisor, service


class GeminiAdvisorTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        service.reset(clear_portal=True)
        service.advance_clock("4d")
        service.evaluate_all()

    def test_ring_investigation_generates_sar_and_structuring_analysis(self):
        deals = service.get_deals()
        flagged = [d for d in deals if d.get("fraud", {}).get("flagged")]
        self.assertEqual(len(flagged), 3)

        investigation = gemini_advisor.investigate_fraud_ring(flagged, deals)
        self.assertIn("verdict", investigation)
        self.assertEqual(investigation["confidence_score"], 0.99)
        self.assertIn("structuring_analysis", investigation)
        self.assertIn("248,500", investigation["structuring_analysis"])
        self.assertIn("sar_narrative", investigation)
        self.assertIn("Pecan Grove Catering", investigation["sar_narrative"])
        self.assertIn("recommended_actions", investigation)
        self.assertGreaterEqual(len(investigation["recommended_actions"]), 3)

    def test_deal_investigation_answers_underwriter(self):
        deals = service.get_deals()
        pecan = next(d for d in deals if d["folder"] == "Pecan Grove Catering")
        response = gemini_advisor.investigate_deal_with_gemini(pecan, "Why is this deal flagged?")
        self.assertTrue(response["flagged"])
        self.assertIn("CRITICAL FRAUD ALERT", response["answer"])
        self.assertIn("Pecan Grove Catering", response["answer"])

    def test_clean_deal_evaluation(self):
        deals = service.get_deals()
        garza = next(d for d in deals if "Garza" in d["folder"])
        response = gemini_advisor.investigate_deal_with_gemini(garza)
        self.assertFalse(response["flagged"])
        self.assertIn("Clean file", response["answer"])


if __name__ == "__main__":
    unittest.main()
