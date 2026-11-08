# Data dictionary

All times are sim time in America/Chicago (UTC−06:00). Money is USD unless a document says otherwise.

## shared_folder/<borrower folder>/

A loan file normally contains (names vary — identify documents by their content):

| Document | What's in it |
|---|---|
| Loan application | Business facts (legal name, DBA, EIN, formation date, address, industry, revenue), the request (amount, product, use of proceeds, term, collateral, funds-needed-by date), guarantor (name, DOB, SSN last 4, home address, driver license no.), signature. |
| Guarantor photo ID | Texas driver license copy (PDF) or a phone photo (JPG). |
| Certificate of formation | State filing: entity name, file number, effective date, principal office, registered agent + address, organizer. |
| IRS EIN letter (CP 575) | Legal name + EIN. |
| Bank statements | One per month. PDF statements have a summary (opening, deposits, withdrawals, closing) and transaction pages marked "Page X of N". Some borrowers send CSV exports instead. |
| Financial statements | P&L (FY2025 and/or YTD Jan–Oct 2026) + balance sheet. "Prepared by" line names the accountant/bookkeeper. |
| Tax return summary | 2025 return: gross receipts, ordinary income, paid preparer + PTIN. Young businesses may send an IRS Form 7004 extension instead. |
| Request letter | The borrower's own words about the request. |
| Equipment quote | Equipment loans: vendor, items, total, and the vendor's remit-to bank/routing/account. |
| Other | Contract summaries, corrections, addenda, bank notices. |

PDF metadata (Producer, Creator, CreationDate) is real data too.

## history/deals.csv

| Column | Meaning |
|---|---|
| deal_id | `H-YYMM-NNN` |
| application_date | date the deal was opened |
| legal_name, ein, industry, naics, formation_date, business_address | borrower facts |
| registered_agent, registered_agent_address | from the state filing |
| guarantor_name, guarantor_dob, guarantor_ssn_last4, guarantor_dl_number | guarantor facts |
| preparer_name, preparer_firm, preparer_phone | who prepared the financials / tax return |
| bank_name | borrower's operating bank |
| amount_requested, product, purpose, term_months, annual_revenue_stated | the request |
| equipment_vendor, vendor_remit_acct_last4 | equipment loans only |
| pdf_producer | the PDF "Producer" of the application packet |
| final_status | funded / declined / withdrawn / fraud_confirmed |
| fraud_label | `none` or an OLD trick code (see known_fraud_checks.md) |
| repayment_status | current / paid_off / 30_days_past_due / charged_off (funded deals, as of 2026-10-31) |
| relationship_since, prior_note | returning-customer context |

`fraud_label = none` means "no known trick found" — not "proven clean".

## history/monthly_deposits.csv

One row per deal per month for the 6 months before application: `total_deposits`, `deposit_count`,
`pct_deposits_multiple_of_100`, `deposit_weekdays` (days deposits usually land), `largest_deposit`, `month_end_balance`.

## history/stage_events.csv

One row per deal per stage: `entered_at`, `exited_at`, `hours_in_stage`, `sla_hours`, `breached`, `alert_sent`,
`escalated`, `decision`, `waiting_on`. Use it to learn what "normal speed" looks like.

## sim/arrivals.jsonl

`{"path": "<folder>/<file>", "arrive_at": "<ISO time>"}` — a file exists for your system only after `arrive_at`.

## sim/assignments.json

`{"<folder>": {"relationship_manager": "<person id>", "credit_officer": "<person id>"}}` — ids are in contacts/teams.json.
