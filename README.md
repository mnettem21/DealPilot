# DealPilot — Team Data Pack (HackUTD 2026)

Everything here is **made up**. No real people, companies, accounts or IDs. Bank: *Trinity Commercial Bank* (fictional).
Emails end in `.example` and phone numbers use the reserved 555-01xx range.

## What's in the box

```
shared_folder/           The bank's shared loan folder: one sub-folder per borrower (13 businesses).
                         PDFs, CSV bank exports, a phone photo of an ID. File names are whatever
                         the borrower used — some are tidy, some are "SCAN_2026…pdf". Read the content.
history/
  deals.csv              One year of past deals (Nov 2025 – Oct 2026), ~257 rows. Old tricks are marked
                         in `fraud_label`. Outcomes + repayment status included.
  monthly_deposits.csv   6 months of bank-deposit summaries before each past deal.
  stage_events.csv       How long every past deal sat in every stage, SLA breaches, alerts.
  known_fraud_checks.md  The bank's existing list of old tricks.
rules/
  stages.json            The pipeline, what each step needs, SLA time limits, human sign-offs, escalation rules.
  known_fraud_checks.md  (copy)
contacts/teams.json      Who does what, backups, who to escalate to, who is out of office.
sim/
  clock.py               The pretend clock. Fast-forward time; files "land" when their time comes.
  arrivals.jsonl         When each file lands in the shared folder (sim time, America/Chicago).
  assignments.json       Relationship manager + credit officer for each deal.
SCHEMA.md                Field-by-field data dictionary.
SUBMISSION_FORMAT.md     The JSON your DealPilot must output for judging.
```

## The sim week

Sim time runs **Mon 2026-11-02 08:00 → Fri 2026-11-06 18:00 CT**. Deals arrive through the week.

```bash
python3 sim/clock.py reset                  # 2026-11-02 08:00
python3 sim/clock.py advance 4h
python3 sim/clock.py inbox --new            # what just landed
python3 sim/clock.py sync live_inbox/       # copy landed files somewhere your app watches
python3 sim/clock.py serve 8765             # or use the HTTP API (GET /now, /inbox · POST /advance?by=6h)
```

Your system should only "see" a file once the clock has passed its `arrive_at`.
Read the real files from `shared_folder/` (that's what `sync` copies).

## Humans

DealPilot must stop and **ask a person** for: credit decisions, committee approval, commitment sign-off,
funding release, and fraud holds (see `rules/stages.json`). During development, play the humans yourself.
During judging, the organizers answer as those people — some answer fast, some are out of office, some
don't answer at all. Read `contacts/teams.json` carefully.

## The weekend drop

Saturday the organizers release **one more drop**: new deals, plus new papers on deals you already have
(and a notice from Collections). The trick in it may look different. Then your system runs on a
**hidden set** with a different trick. *Build for the idea, not the examples.*

## How you'll be scored (summary)

| Score | What earns points |
|---|---|
| **READ** | Correct borrower name, EIN, amount, purpose, product, term, guarantor, stated revenue — and spotting what is missing or doesn't match (no false alarms). |
| **MOVE** | Stages in order, the right human asked before each sign-off step, alerts + escalations on stuck deals, not pushing a deal forward while it waits on a document, hitting "funds needed by" dates. |
| **FRAUD** | Fakes held **before** money goes out (earlier = more points), the pattern spotted in the first few deals, good deals **not** blocked (each false flag costs a lot), and "finding the rest" — linking related deals, including old ones in `history/`. |

Have fun. One deal can look fine. Look across all the deals.

---

## DealPilot application

This repository now includes a working local-first DealPilot web application. It reads the timed
borrower arrivals, extracts evidence from PDFs and CSVs, blocks incomplete or inconsistent deals,
routes required human approvals, monitors SLAs, detects cross-deal fraud networks, and exports the
required submission JSON.

It now has two connected experiences:

- **Borrower portal:** account creation, secure sign-in, a five-step business/loan/guarantor flow,
  multi-file evidence upload, saved drafts, submission review, an approval-readiness result, and a
  portable manifest containing SHA-256 document checksums.
- **Bank workspace:** role-bound queues for credit, committee, relationship management, Fraud Ops,
  funding, and administration; evidence passports; human decisions; fraud relationships; SLAs; and
  the challenge submission exporter.

### Run it

```bash
python3 -m pip install -r requirements.txt
python3 -m dealpilot
```

Open `http://127.0.0.1:8765`. Use **Advance 4 hours** or **Advance 1 day** to move through the
simulated week. On the entry page, a borrower can create an account. Selecting **Open bank sign-in**
opens a separate employee login; it never creates a bank session automatically. The server—not the
UI—enforces which role can enter the bank workspace and which human gate that role may complete.
Choose **Export submission** to write `output/submission.json`.

Bank accounts are seeded without usable passwords. Provision or rotate one through the hidden
terminal prompt (the password is not echoed or logged):

```bash
python3 -m dealpilot.bank_admin admin@trinitybank.example
```

Other seeded addresses are `credit@trinitybank.example`, `committee@trinitybank.example`,
`rm@trinitybank.example`, `fraud@trinitybank.example`, and `funding@trinitybank.example`.

### Approval-readiness policy

The number shown to users is an **approval-readiness score**, where a higher number means a safer,
more complete application. It is not a fraud-risk score.

- `90–100`: fast-track pre-qualification for the requested amount.
- `70–89`: human review with a suggested amount capped at 50% of the request.
- `<70`: decline recommendation, a bank-reviewable notice draft, and a 90-day reapplication window.

Every tier still requires a human confirmation. DealPilot never autonomously releases funds, sends
an adverse-action notice, or uses protected attributes in the score.

### Verify it

```bash
python3 -m unittest discover -s tests -v
```

The tests cover the 13-deal ingestion, missing-document and mismatch blockers, the coordinated
three-company fraud ring and its historical links, a human-approved fraud hold, a clean deal moving
through every gate to `Closed`, password derivation, and all three approval-score bands.

### Security model

- Borrower documents stay local in an application-specific isolated directory; the extraction
  pipeline does not send them to an external model. Local database and upload permissions are
  owner-only where the operating system supports POSIX permissions.
- SQLite uses foreign keys, transactions, parameterized statements, and an append-only audit trail.
- Passwords use a unique random salt and PBKDF2-HMAC-SHA256 with 310,000 iterations. Raw passwords
  are never stored, returned by an API, displayed by the app, or written to logs. Plain SHA-256 is
  deliberately not used because it is unsafe for passwords.
- Session tokens are random, stored only as SHA-256 token hashes, and delivered in HttpOnly,
  SameSite=Strict cookies. The local demo cookie omits `Secure` because it runs over localhost HTTP;
  production must use HTTPS and the `Secure` flag.
- Uploads are allow-listed by extension, limited to 10 MB, renamed server-side, content-classified,
  checksum-recorded, and prevented from being served as executable content.
- Human actions are role-gated and every approval records actor, evidence version, and sim time.
- New evidence invalidates earlier non-fraud approvals so stale decisions cannot fund a changed deal.
- The server binds to `127.0.0.1` by default. Production deployment should add SSO/MFA, TLS, a
  managed secrets store, encrypted object storage, malware scanning, rate limiting, and centralized
  security monitoring.
