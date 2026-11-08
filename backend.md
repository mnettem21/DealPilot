# DealPilot backend

This is the handoff for the stage services. PocketBase is the only database. The app is one Python package, started as several processes. A `--stage` process loads only that stage's module, so Intake code never runs Credit checks.

`web/` is not connected to these services. There is no fraud detector and no local SQLite database. A fraud flag is a row someone inserts. The submission file is produced by `scripts/submission.py`.

## Shape

```text
sim clock + shared_folder
        |
        v
     parser ----> current stage collection
                       |
          human ------>|  (records a decision, does not move the row)
                       v
                     rules ----> next stage collection
                       |
                       +----> actions
     sla --------------> alerts
     history ----------> history          (only when funded or declined)
     upload ----------> files             (parser does not read these)
```

| Process | Command | Listens |
|---|---|---|
| Parser | `python3 -m services.parser --stage <stage>` | no |
| Rules | `python3 -m services.rules --stage <stage>` | no |
| Human | `python3 -m services.human --stage <stage>` | `8201` intake, then +1 per stage |
| Upload | `python3 -m services.upload --stage <stage>` | `8101` intake, then +1 per stage |
| SLA | `python3 -m services.sla --stage <stage>` | no |
| History | `python3 -m services.history` | no |

Stage order, and the human / upload ports:

| Stage | Human | Upload |
|---|---|---|
| intake | 8201 | 8101 |
| credit | 8202 | 8102 |
| committee | 8203 | 8103 |
| product | 8204 | 8104 |
| committed | 8205 | 8105 |
| closed | 8206 | 8106 |

Parser, rules, SLA, and history poll every 5 seconds unless you pass `--once`. Change the wait with `--interval`. Human and upload stay up until you stop them. Each of parser, rules, human, upload, and SLA is one process per stage. An Intake rules process will not move a Credit deal. History is a single process for every stage.

## Run

`.env` in the repo root:

| Variable | Use |
|---|---|
| `POCKETBASE_URL` | PocketBase base URL |
| `POCKETBASE_EMAIL` | Admin login |
| `POCKETBASE_PASSWORD` | Admin password |
| `DEALPILOT_TEAM` | Team name written into the submission JSON |

```bash
python3 -m pip install -r requirements.txt
python3 -m services.bootstrap
```

`services/bootstrap.py` deletes every non-system PocketBase collection, then recreates the tables below and reloads contacts and the past history book. Running it again wipes live deals.

```bash
python3 sim/clock.py reset
python3 sim/clock.py advance 2h
python3 -m services.parser --stage intake --once
python3 -m services.rules --stage intake --once
```

Leave the flag off to keep a process running:

```bash
python3 -m services.parser --stage intake
python3 -m services.rules --stage credit
python3 -m services.human --stage credit
```

File arrival and SLA hours use sim time from `sim/clock.py` (`sim/.clock_state.json`), not the wall clock. Nothing advances the clock for you.

## How a deal moves

A folder has one row, in exactly one stage collection. The first shared-folder file that the sim clock says has arrived creates the `intake` row. Later parsers only touch rows already in their own stage.

A human answer writes an `actions` row and sets `status`. It does not move the deal. The rules process for that stage reads the latest human decision and moves the row: it deletes it from the current collection and inserts it in the next one, and writes `stage_exit` plus `stage_enter`.

| Answer | What rules do |
|---|---|
| `approve` | Move to the next stage. On Closed, set `status=funded` instead of moving. |
| `decline` | Leave the row where it is with `status=declined`. |
| `request_info` | Leave the row where it is and set `waiting_on`. Credit is the only stage that accepts this answer. |

`status` is `open`, `waiting_human`, `approved`, `declined`, or `funded`. Declined and funded deals drop out of SLA.

Credit is the only stage that starts a container. When a Credit approval is ready to move the deal to Committee, rules copy that deal's files and run:

```bash
docker run --rm -v <deal-files>:/data dealpilot-files
```

The image is `Dockerfile.files`. It only lists `/data`. Rules build the `dealpilot-files` tag if it is missing. On this Mac, Docker is started with `posix_spawn` because `fork` crashes after the PocketBase client has used TLS.

## Tables

### Stage queues

`intake`, `credit`, `committee`, `product`, `committed`, `closed` share one shape. `folder` is unique inside each collection.

| Column | Meaning |
|---|---|
| `folder` | Borrower folder name. This is the deal id. |
| `entered_at` | Sim time the row entered this stage. |
| `status` | `open`, `waiting_human`, `approved`, `declined`, or `funded`. |
| `assignee` | Person id expected to act, or the stage owner when there is no human gate. |
| `extracted` | JSON facts the parser has merged so far. Also holds `relationship_manager` and `credit_officer` from `sim/assignments.json`. |
| `waiting_on` | What blocks the deal right now: a doc code, a human decision type, or `fraud detected`. |
| `memo` | Credit memo. Written when the row enters Committee. Empty on the other stages. |

### files

One row per file that belongs to a deal. `path` is unique.

| Column | Meaning |
|---|---|
| `folder` | Deal folder. |
| `path` | Path relative to the repo. |
| `filename` | File name. |
| `arrived_at` | Sim time from `sim/arrivals.jsonl`, or the upload time. |
| `doc_type` | Classifier label, such as `loan_application` or `bank_statement`. |
| `source` | `shared` or `upload`. |

The parser only registers a `shared` file after `arrive_at` is at or before the sim clock, and only if that stage's module cares about the doc type. Uploads are stored under `data/uploads/<folder>/` and recorded with `source=upload`. The parser does not read them. The Credit container still prints them, because they belong to the deal.

### actions

The log of movement and human decisions. There is no unique key. Rules write `stage_exit` and `stage_enter`. The parser writes the first `stage_enter` for Intake. Humans write `kind=decision`.

| Column | Meaning |
|---|---|
| `at` | Sim time. |
| `folder` | Deal folder. |
| `stage` | Lowercase stage name. |
| `actor` | `parser`, `rules`, or a person id. |
| `kind` | `stage_enter`, `stage_exit`, or `decision`. |
| `decision` | `entered`, `advanced`, `approve`, `decline`, `request_info`, or `funds disbursed`. |
| `why` | Short reason. |

### alerts

One row per folder, stage, level, and recipient.

| Column | Meaning |
|---|---|
| `at` | Sim time. |
| `folder` | Deal folder. |
| `stage` | Lowercase stage name. |
| `level` | `alert` or `escalation`. |
| `to` | Person id. |
| `message` | Who the deal is, the RM, the stage, how long it has waited, what it is waiting on, and that resolving that wait unblocks it. |

### contacts

Loaded once by bootstrap from `contacts/teams.json`. SLA and assignee lookups read this table, not the JSON file.

| Column | Meaning |
|---|---|
| `person_id` | Id used everywhere else, such as `marcus.lee`. Unique. |
| `name`, `team`, `role`, `email`, `chat`, `phone` | Directory fields. |
| `backup` | Person id who covers an out-of-office note. |
| `escalates_to` | Person id for the 150% SLA escalation. |
| `notes` | Free text. An `OUT OF OFFICE <start> through <end>` note swaps the credit assignee to `backup`. |

### fraud_detected

One row per deal. `folder` is unique. This table does not detect anything. `scripts/fraud.py` inserts the row. Flagging the same folder again does not add a second row.

| Column | Meaning |
|---|---|
| `folder` | Deal folder. |
| `at` | Sim time the flag was written. |
| `stage` | Queue the deal was in when it was flagged. |
| `reason` | Text stored with the flag. |

Only Closed rules read this table. If the row exists, funding does not run and `waiting_on` becomes `fraud detected`. Earlier stages keep advancing. Deleting the row allows a later funding run. A decline in Closed still stops the deal.

### history

Bootstrap loads `history/deals.csv` into this table. Those rows use `deal_id` values like `H-2511-001`. The history service appends one live row when a deal's status becomes `funded` or `declined`, using the folder name as `deal_id`. The stage row stays where it is. Past `H-…` rows are not updated.

Columns, in CSV order: `deal_id`, `application_date`, `legal_name`, `ein`, `industry`, `naics`, `formation_date`, `business_address`, `registered_agent`, `registered_agent_address`, `guarantor_name`, `guarantor_dob`, `guarantor_ssn_last4`, `guarantor_dl_number`, `preparer_name`, `preparer_firm`, `preparer_phone`, `bank_name`, `amount_requested`, `product`, `purpose`, `term_months`, `annual_revenue_stated`, `equipment_vendor`, `vendor_remit_acct_last4`, `pdf_producer`, `final_status`, `fraud_label`, `repayment_status`, `relationship_since`, `prior_note`.

Live rows fill the fields the parser actually extracted. The rest stay empty. `final_status` is `funded` or `declined`.

## Stage rules

Code for a stage lives in `services/stages/<stage>.py`. A process passes `--stage` and only that module's docs, blockers, and human answers apply.

- **Intake** (8 hours, assignee `olivia.chen`, no human). Parser reads the signed application, guarantor photo ID, certificate of formation, and EIN letter, and pulls legal name, EIN, amount, purpose, product, term, guarantor, stated revenue, and needed-by. Rules move the row to `credit` when those docs are present and the name, EIN, and guarantor agree.
- **Credit** (48 hours). Assignee is the deal's credit officer, or that person's `backup` while their out-of-office note covers the sim clock. Parser reads bank statements for 2026-05 through 2026-10, financial statements, and a tax return. A business under two years old may have Form 7004 instead of the return. Known-fraud checks are not run. Human answers are `approve`, `decline`, and `request_info`. On approve, this rules process prints the deal's files, then moves the row to `committee`.
- **Committee** (24 hours at or under $249,999.99, otherwise 48). Entering the stage writes `memo` from `extracted`. Assignee is `priscilla.nguyen` at or under that amount, otherwise `harold.benson`. Human answers are `approve` and `decline`. Approve moves the row to `product`.
- **Product** (16 hours, assignee `kevin.alvarez`, no human). The product must be Term Loan, Equipment Loan, Revolving Line of Credit, or Working Capital Loan, and it must match an equipment purpose. Equipment loans need a vendor quote, and the quote total cannot exceed the request. Rules then move the row to `committed`. Run the Product parser before the Product rules when the loan is an equipment loan, or the quote is not on the row yet.
- **Committed** (48 hours). Assignee is the deal's relationship manager from `extracted`. Human `commitment_signoff` is `approve` or `decline`. Approve moves the row to `closed`.
- **Closed** (24 hours, assignee `tasha.reed`). Human `funding_release` `approve` makes rules set `status=funded` and log `decision=funds disbursed`. The row stays in `closed`. If `fraud_detected` has that folder, funding does not run. Only the Closed SLA changes its message to say fraud is holding the deal up, and it includes the stored reason. If that Closed alert already exists, its `message` is updated.

SLA, for whichever stage you started: at 100% of the limit, alert the assignee and the relationship manager. At 150%, escalate to the assignee's `escalates_to`. At 200%, escalate to `lauren.kim`.

## Scripts

The human service for that stage must already be running.

```bash
python3 scripts/human.py --stage credit list
python3 scripts/human.py --stage credit answer "Folder Name" approve --actor marcus.lee
```

`list` shows `folder`, `status`, `assignee`, `waiting_on`, and `memo`. Pass the assignee as `--actor`. `approve` records the decision. Run that stage's rules process, or leave it running, to move the row.

```bash
python3 scripts/fraud.py flag "Folder Name" "reason"
```

```bash
python3 scripts/submission.py
python3 scripts/submission.py --set sample --out output/submission.json
```

The submission file follows `SUBMISSION_FORMAT.md`. `team` is `DEALPILOT_TEAM`. Each deal is a folder still sitting in a stage queue. `issues` are only the current `waiting_on` codes (`MISSING_DOC`, `MISSING_PAGE`, `MISMATCH`, `OLD_TRICK`). Blockers that were cleared when the deal advanced are not in the file. `linked_deals` and `history_flags` are empty because no table links a live deal to another folder or an `H-…` history id.

## Code map

| Path | Role |
|---|---|
| `services/bootstrap.py` | Wipe and create collections. Load contacts and past history. |
| `services/common/` | PocketBase client, sim clock, move-row helper, people, fraud lookup. |
| `services/stages/` | One module per stage: docs, blockers, assignee, human answers. |
| `services/parser/` | Read arrived shared-folder files for one stage. |
| `services/rules/` | Apply that stage's exit rules. Credit also prints files. |
| `services/human/` | HTTP API for that stage's answers. |
| `services/upload/` | HTTP upload onto a deal already in that stage. |
| `services/sla/` | SLA pass for one stage. |
| `services/history/` | Append funded and declined deals onto `history`. |
| `services/parser/extract.py` | PDF and CSV field extraction. |

## Not in this backend

- `web/` is still the old UI. It does not call these services.
- There is no fraud detector. `fraud_detected` only holds rows written by `scripts/fraud.py`.
- There is no local SQLite file and no `db.py`.
- `rules/`, `contacts/`, `history/`, `sim/`, and `shared_folder/` stay on disk as inputs the bootstrap and the parser read.
