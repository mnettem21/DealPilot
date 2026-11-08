import os
import tempfile
import unittest
from unittest import mock
from pathlib import Path

from services.common.env import ROOT
from services.parser.extract import normalize_model, process_document
from services.parser.llm import ModelError, complete_json, parse_json_object


APPLICATION = ROOT / "shared_folder" / "Summit Peak Flooring" / "01_Loan_Application.pdf"


class NormalizeTests(unittest.TestCase):
    def test_loan_application_numbers_and_category(self):
        document_type, facts = normalize_model(
            {
                "document_type": "loan_application",
                "legal_name": "Summit Peak Flooring LLC",
                "ein": "992290418",
                "amount_requested": "$246,000.00",
                "product": "Equipment Loan",
                "purpose": "Purchase floor-prep machines",
                "term_months": "48 months",
                "annual_revenue_stated": "approx 400-500k",
                "guarantor_dl_number": "TX 41872246",
                "formation_date": "2025-08-04",
                "needed_by": "2026-11-20T15:00",
            },
            "",
            "01_Loan_Application.pdf",
        )
        self.assertEqual(document_type, "loan_application")
        self.assertEqual(facts["ein"], "99-2290418")
        self.assertEqual(facts["amount_requested"], 246000.0)
        self.assertEqual(facts["term_months"], 48)
        self.assertEqual(facts["purpose_category"], "equipment")
        self.assertNotIn("annual_revenue_stated", facts)
        self.assertIn("400-500k", facts["annual_revenue_stated_text"])
        self.assertEqual(facts["guarantor_dl_number"], "41872246")
        self.assertEqual(facts["needed_by"], "2026-11-20T15:00")

    def test_model_category_used_when_keywords_miss(self):
        _document_type, facts = normalize_model(
            {
                "document_type": "loan_application",
                "purpose": "Bridge a slow season",
                "purpose_category": "Refinance",
            },
            "",
            "app.pdf",
        )
        self.assertEqual(facts["purpose_category"], "refinance")

    def test_statement_month_and_page_markers(self):
        document_type, facts = normalize_model(
            {
                "document_type": "bank_statement",
                "opening_balance": "118994.98",
                "total_deposits": 296038.58,
                "closing_balance": "$176,730.99",
            },
            "Opening\nPage 1 of 2\nTRANSFER TO XXXX8810\nPage 2 of 2\n",
            "may statement.pdf",
        )
        self.assertEqual(document_type, "bank_statement")
        self.assertEqual(facts["statement_month"], "2026-05")
        self.assertEqual(facts["declared_pages"], 2)
        self.assertEqual(facts["seen_page_markers"], [1, 2])
        self.assertEqual(facts["transfer_accounts"], ["8810"])
        self.assertEqual(facts["opening_balance"], 118994.98)
        self.assertEqual(facts["total_deposits"], 296038.58)
        self.assertEqual(facts["closing_balance"], 176730.99)

    def test_unknown_type_is_rejected(self):
        with self.assertRaises(ValueError):
            normalize_model({"document_type": "passport"}, "", "scan.pdf")


class LlmTests(unittest.TestCase):
    def test_parse_fenced_json(self):
        self.assertEqual(parse_json_object('```json\n{"a": 1}\n```'), {"a": 1})

    def test_retries_when_the_body_is_not_json(self):
        calls = []

        def fake_chat(messages, timeout=None):
            calls.append(messages)
            if len(calls) == 1:
                return "I think this is a loan application."
            return '{"document_type": "other"}'

        with mock.patch("services.parser.llm.chat", side_effect=fake_chat):
            self.assertEqual(complete_json([]), {"document_type": "other"})
        self.assertEqual(len(calls), 2)


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self._cache = os.environ.get("EXTRACT_CACHE_DIR")
        self.temp = tempfile.TemporaryDirectory()
        os.environ["EXTRACT_CACHE_DIR"] = self.temp.name

    def tearDown(self):
        self.temp.cleanup()
        if self._cache is None:
            os.environ.pop("EXTRACT_CACHE_DIR", None)
        else:
            os.environ["EXTRACT_CACHE_DIR"] = self._cache

    def test_http_failure_uses_the_label_matcher(self):
        with mock.patch(
            "services.parser.extract.llm.complete_json",
            side_effect=ModelError("down"),
        ):
            parsed = process_document(APPLICATION)
        self.assertEqual(parsed["document_type"], "loan_application")
        self.assertEqual(parsed["facts"]["legal_name"], "Summit Peak Flooring LLC")
        self.assertEqual(parsed["facts"]["amount_requested"], 246000.0)
        self.assertEqual(parsed["facts"]["purpose_category"], "equipment")
        self.assertEqual(list(Path(self.temp.name).glob("*.json")), [])

    def test_bad_type_uses_the_label_matcher(self):
        with mock.patch(
            "services.parser.extract.llm.complete_json",
            return_value={"document_type": "passport"},
        ):
            parsed = process_document(APPLICATION)
        self.assertEqual(parsed["document_type"], "loan_application")
        self.assertEqual(parsed["facts"]["ein"], "99-2290418")

    def test_successful_call_is_cached(self):
        calls = {"n": 0}

        def fake(_messages):
            calls["n"] += 1
            return {
                "document_type": "loan_application",
                "legal_name": "Summit Peak Flooring LLC",
                "ein": "99-2290418",
                "amount_requested": 246000,
                "product": "Equipment Loan",
                "purpose": "Purchase equipment",
                "term_months": 48,
            }

        with mock.patch("services.parser.extract.llm.complete_json", side_effect=fake):
            first = process_document(APPLICATION)
            second = process_document(APPLICATION)
        self.assertEqual(calls["n"], 1)
        self.assertEqual(first["facts"]["purpose_category"], "equipment")
        self.assertEqual(second["checksum"], first["checksum"])
        self.assertEqual(second["facts"]["ein"], "99-2290418")
        self.assertEqual(len(list(Path(self.temp.name).glob("*.json"))), 1)


if __name__ == "__main__":
    unittest.main()
