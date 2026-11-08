# Detection agent

Check one deal's documents, compare them with `history/deals.csv`, print one JSON result, and exit. A new suspicious pattern is a **warning**. A confirmed fraud pattern is **critical**. A warning does not hold the deal.

The type list is `.cursor/skills/flag-fraud/fraud-types.json`. `confirmed: true` is critical. `confirmed: false` with `level: warning` stays a warning until a person accepts it. Do not invent a second list.

## Documents

Read every file mounted at `/data`. The folder name is `DEAL_FOLDER`. Pull the vendor name and the remit-to account from the equipment quote, and the borrower facts the other checks need (legal name, EIN, revenue, bank-statement balances, photo ID).

## History, vendor names, and payment accounts

Compare this deal with past rows, especially `equipment_vendor` and `vendor_remit_acct_last4`.

The established Metroplex payee is `Metroplex Equipment Supply, Inc.` paid to account `9902`. Many funded history deals use that pair. A deal that uses that name and that account is the real vendor. Do not flag it for the lookalike pattern. Oakline Auto is that case.

The suspected pattern is a near-name, `Metroplex Equipment Supply LLC`, paid to account `4471`. History already has it on `H-2608-002` (Trinity Bend Plumbing) and `H-2609-008` (Mesquite Ridge Signs). The same pair shows up on Ridgeway Freight Logistics, Pecan Grove Catering, and Summit Peak Flooring. That three-deal reading is an interpretation of the data. It is **not confirmed**. Return `warning`, set `hold` to false, and name the vendor, the account, and the history ids. Do not return `critical` for this pattern, and do not write a `fraud_detected` row.

Treat any other unconfirmed cross-deal signal the same way: a dependency break, a ring link, a rare neighbour, or a reused identity is a warning until fraud review marks that pattern confirmed.

## Confirmed fraud

Return `critical` when a finding's code is `confirmed: true` in the catalog. Leave `hold` false. The agent does not stop a deal and does not write a queue row. That is the bank's existing list (`STMT_TAMPER`, `REV_INFLATION`, `ID_MISMATCH`, `DUP_EIN`, `SHELL_ADDRESS`) and `LEARNED_RULE` after a person has already accepted the pattern. Granite Bay Building Supply is critical because its statements and revenue trip confirmed types, not because of a vendor lookalike.

If a deal has both a confirmed type and a new pattern, the result level is `critical`.

A deal with no confirmed type and no new pattern is `level: none`. Do not warn a borrower just because an equipment vendor is unfamiliar when the name and account do not match a suspected pattern. Westfield Print & Sign pays Lone Star Graphic Systems and stays clear.

## Result

Print one JSON object to stdout and exit 0:

```json
{
  "folder": "<DEAL_FOLDER>",
  "level": "none",
  "hold": false,
  "findings": [],
  "linked_deals": [],
  "signals": {"vendor": null, "remit_acct_last4": null},
  "reason": ""
}
```

`findings` entries are `{"rule", "level", "confirmed", "evidence"}`. `hold` is always false. Info-only signals (`STYLE_ANOMALY`, `DOC_FORENSICS`) stay out of `findings` and do not change `level`.

The pipeline, not this agent, files the result. `critical` becomes one `fraud_critical` row labeled `fraud critical`. The deal keeps moving until Committed, then sits there until the fraud team releases it toward Closed or rejects it. `warning` becomes one `fraud_warnings` row. That table does not stop the deal, and the deal can close if the fraud team never opens it.

When `PIPELINE_URL` is set, POST that JSON to it with `Content-Type: application/json` and `Authorization: Bearer <API_TOKEN>`. `API_TOKEN` is passed with `docker run -e API_TOKEN=...` and is not written into the image. History is mounted at `/opt/dealpilot/history` because `history/` is excluded from the image build. If the callback is not set, stdout is the whole result.
