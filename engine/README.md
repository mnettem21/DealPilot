# DealPilot (HackUTD 2026)

Reads loan folders, moves each deal Intake → Closed, asks humans at sign-off gates, alerts/escalates stuck deals,
and flags fraud rings by linking *rare* shared attributes across all deals (current + `data/history`).

```bash
python3 -m dealpilot.app --port 8781      # web UI: advance the sim clock, answer requests with buttons
python3 -m dealpilot.cli run              # headless: whole sample week, auto-answering humans -> submission.json
python3 -m pytest -q
```
Hidden / weekend set: `--data <dir> --arrivals <arrivals.jsonl>` (same layout as `data/`). An optional local LLM (Ollama, `OLLAMA_MODEL`, default llama3.1:8b) fills only fields the rules miss and polishes fraud text;
nothing leaves the machine. Needs `pdftotext`, `pdfinfo`, `tesseract` on PATH.

Tuning knobs for fraud linking: `LINK_SUM`, `TYPE_MIN_W` in `dealpilot/fraud.py`.

## Persistence (PocketBase)
History from earlier runs, Fraud Ops labels (confirmed / cleared) and learned patterns (proposed / active / rejected) live in a store:
`dealpilot/store.py`. Default is local JSON under `state/store/`. To use PocketBase set `POCKETBASE_URL` and either `POCKETBASE_TOKEN`
or `POCKETBASE_EMAIL` + `POCKETBASE_PASSWORD` (superuser). Create the three collections (`deals`, `labels`, `rules`; each has `key`, `status`,
`data` json) with `python3 -m dealpilot.store` (prints an import file, PocketBase v0.23+ format; not yet tried against a live instance).
A deal is written to `deals` only when it reaches Closed, Declined or Withdrawn; a deal on hold stays open until Fraud Ops releases it
(it resumes) or confirms fraud (declined, kept as a known fraud).

## Where this lives in the repo
This folder (`engine/`) is self-contained. It uses the repo-root data pack (`shared_folder`, `history`, `rules`, `contacts`, `sim`) unless `engine/data/` exists.
Run from `engine/`:  `python3 -m dealpilot.cli check --expect tests/expected_sample.json`  (add `--data <participant folder>` for another drop; no `--expect` for a hidden set)
Tests: `python3 -m pytest -q`   (set `HIDDEN_DIR=<folder> [HIDDEN_KEY=key.json]` to include a hidden drop)
