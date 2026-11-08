---
name: flag-fraud
description: >-
  Flags a DealPilot deal by writing one fraud_detected row through the PocketBase
  backend in backend.md. Reads known fraud types from fraud-types.json (STMT_TAMPER,
  REV_INFLATION, ID_MISMATCH, DUP_EIN, SHELL_ADDRESS, DEPENDENCY_BREAK,
  IDENTITY_REUSE, RING_LINK, PATTERN_NEIGHBOUR, LEARNED_RULE, and info-only
  signals). Use when sending a fraud request, flagging a folder, recording fraud,
  holding funding for fraud, or when any other part of the system needs the
  known fraud type list.
---

# Flag fraud

Known fraud types live in [fraud-types.json](fraud-types.json) next to this file. That file is the list the rest of the system reads (`services.common.fraud_types`, and the engine's old-trick set). Do not invent a second list.

A fraud request becomes one row in PocketBase `fraud_detected`. Closed rules are the only stage that read it: if the row exists, funding does not run and `waiting_on` becomes `fraud detected`. The Closed SLA includes the stored reason. Flagging the same folder again does not add a second row. Deleting the row allows a later funding run.

## Types

Read `fraud-types.json` before classifying.

| `confirmed` | `level` | What to do |
|---|---|---|
| `true` | `critical` | Confirmed fraud. `family` `old` is the bank's list. `LEARNED_RULE` is a pattern a person already accepted. Insert one `fraud_detected` row. |
| `false` | `warning` | A new pattern (vendor/account lookalike, ring, neighbour, identity reuse). Hand it to fraud review. Do not insert `fraud_detected` and do not hold the deal. |
| `false` | `info` | `STYLE_ANOMALY` and `DOC_FORENSICS`. Report only. |

Match the evidence to one code. If two codes match, flag the one whose `level` is `critical`. Put the other code in the reason text. A code that is not in the file is not a known type: do not insert a row for it; say which codes were considered.

## Send the request

The deal must already be in a stage queue (`intake`, `credit`, `committee`, `product`, `committed`, or `closed`). From the repo root:

```bash
python3 scripts/fraud.py flag "<folder>" --type <CODE> --reason "<evidence>"
```

`<folder>` is the borrower folder name, the deal id. `<CODE>` is a `writes_row` code from the catalog. `<evidence>` is the short fact that tripped it (amounts, months, the other folder or history id). The script writes `folder`, sim time, the stage the deal is in now, and `reason` as `<CODE>: <evidence>`.

```bash
python3 scripts/fraud.py flag "Granite Bay Building Supply" --type STMT_TAMPER --reason "2026-06 opening does not equal 2026-05 closing"
```

A plain reason still works when the type is already named in the text:

```bash
python3 scripts/fraud.py flag "Granite Bay Building Supply" "STMT_TAMPER: 2026-06 opening does not equal 2026-05 closing"
```

Prefer `--type` so the code is checked against the catalog.

## After it writes

- `fraud: flagged <folder> in <stage>` means the row is in. Stop.
- `fraud: <folder> is already flagged` means the row is already there. Do not update it and do not insert another.
- `<folder> is not in a stage queue` means the parser has not created the deal yet. Do not invent a row.
- An unknown code or a `writes_row: false` code exits without writing.
