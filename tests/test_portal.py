import base64
import unittest

from dealpilot import auth, consumer


class PortalSecurityAndDecisionTests(unittest.TestCase):
    def setUp(self):
        self.user = {
            "display_name": "Jordan Lee",
            "organization": "Example Supply LLC",
        }

    def test_password_derivation_is_salted_pbkdf2_sha256(self):
        password = "StrongPassword123"
        first_salt = b"0123456789abcdef"
        second_salt = b"fedcba9876543210"
        first = auth.password_digest(password, first_salt)
        self.assertNotEqual(first, auth.password_digest(password, second_salt))
        self.assertNotIn(password, first)
        self.assertEqual(len(base64.b64decode(first)), 32)

    def test_high_score_fast_tracks_but_keeps_human_control(self):
        deal = {"issues": [], "fraud": {"flagged": False}, "extracted": {"amount_requested": 100_000}}
        score, decision = consumer.score_application(deal, self.user)
        self.assertEqual(score["approval_readiness"], 100)
        self.assertEqual(score["tier"], "FAST_TRACK")
        self.assertTrue(decision["requires_human_confirmation"])

    def test_middle_score_suggests_half_amount_for_review(self):
        deal = {
            "issues": ["MISSING_DOC:tax_return", "MISSING_DOC:financial_statements"],
            "fraud": {"flagged": False}, "extracted": {"amount_requested": 120_000},
        }
        score, decision = consumer.score_application(deal, self.user)
        self.assertEqual(score["approval_readiness"], 84)
        self.assertEqual(score["tier"], "HUMAN_REVIEW")
        self.assertEqual(score["suggested_amount"], 60_000)
        self.assertEqual(decision["application_status"], "human_review")

    def test_low_score_creates_reviewable_reapplication_notice(self):
        deal = {
            "issues": ["MISMATCH:ein", "OLD_TRICK:REV_INFLATION", "MISSING_PAGE:bank_statement_2026-08"],
            "fraud": {"flagged": True}, "extracted": {"amount_requested": 90_000},
        }
        score, decision = consumer.score_application(deal, self.user)
        self.assertLess(score["approval_readiness"], 70)
        self.assertEqual(score["tier"], "DECLINE_RECOMMENDED")
        self.assertEqual(decision["reapply_after_days"], 90)
        self.assertIn("must be reviewed by a bank employee", decision["email_draft"])
        self.assertFalse(score["protected_attributes_used"])


if __name__ == "__main__":
    unittest.main()
