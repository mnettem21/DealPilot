# Submission format

At the end of a run (sample week, weekend drop, or hidden set) your DealPilot writes **one JSON file**.
Key each deal by its **folder name** in `shared_folder/`. Times are ISO-8601 sim times.

```json
{
  "team": "Team Name",
  "set": "sample",
  "deals": {
    "<folder name>": {
      "extracted": {
        "legal_name": "Example Widgets LLC",
        "ein": "12-3456789",
        "amount_requested": 120000,
        "purpose_category": "equipment",
        "product": "Equipment Loan",
        "term_months": 60,
        "guarantor_name": "Jane Q. Example",
        "annual_revenue_stated": 1500000,
        "needed_by": "2026-11-06T15:00-06:00"
      },
      "issues": ["MISSING_DOC:tax_return", "MISMATCH:ein"],
      "stage_log": [
        {"stage": "Intake", "entered_at": "2026-11-02T09:30-06:00", "exited_at": "2026-11-02T11:00-06:00",
         "decision": "advanced", "why": "all intake docs present; facts consistent"}
      ],
      "human_requests": [
        {"type": "credit_decision", "to": "marcus.lee", "at": "2026-11-02T15:00-06:00", "message": "plain-words summary…",
         "answer": "approve", "answered_at": "2026-11-02T16:10-06:00"}
      ],
      "alerts": [
        {"at": "2026-11-03T20:00-06:00", "stage": "Credit", "level": "alert", "to": "aisha.grant",
         "message": "whose deal, waiting on what, for how long, what unblocks it"}
      ],
      "fraud": {
        "flagged": true, "flagged_at": "2026-11-03T11:30-06:00", "stage_at_flag": "Intake", "held": true,
        "reason": "evidence in plain words", "linked_deals": ["<other folder>", "H-2601-004"]
      }
    }
  },
  "history_flags": ["H-…"]
}
```

## Allowed values

* **stage**: `Intake`, `Credit`, `Committee`, `Product`, `Committed`, `Closed` (entering `Closed` = money goes out).
  A held / declined / withdrawn deal simply stops; say why in `decision`.
* **purpose_category**: `working_capital`, `equipment`, `inventory`, `payroll`, `expansion`, `refinance`.
* **product**: `Term Loan`, `Equipment Loan`, `Revolving Line of Credit`, `Working Capital Loan` (as requested by the borrower).
* **human_requests.type**: `credit_decision`, `committee_approval`, `commitment_signoff`, `funding_release`, `fraud_hold`,
  `fraud_release`, `info_request` (to the borrower use `"to": "borrower"`).
* **alerts.level**: `alert` (to the owner) or `escalation` (to their manager / next level).
* **issues** — one string per problem, `KIND:target`:
  * `MISSING_DOC:<doc>` / `MISSING_PAGE:<doc>` — doc ∈ `application`, `guarantor_id`, `certificate_of_formation`, `ein_letter`,
    `bank_statement_YYYY-MM`, `financial_statements`, `tax_return`, `equipment_quote`
  * `MISMATCH:<field>` — field ∈ `legal_name`, `ein`, `amount_requested`, `guarantor_name`, `guarantor_dob`,
    `guarantor_address`, `annual_revenue`, `bank_balance`
  * `OLD_TRICK:<code>` — code from `rules/known_fraud_checks.md`
* **linked_deals / history_flags**: folder names of current deals and/or `H-…` ids of history deals that you believe belong
  to the same scheme. This is how "if one slips through, help the team find the rest" is scored.

Fields you cannot determine: use `null`. Extra keys are ignored, so add whatever helps your demo.
