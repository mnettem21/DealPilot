import json
import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime

from .config import DATA_DIR, DB_PATH, SIM_START


SCHEMA = """
PRAGMA journal_mode=WAL;
PRAGMA foreign_keys=ON;

CREATE TABLE IF NOT EXISTS app_state (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS deals (
  id TEXT PRIMARY KEY,
  folder TEXT UNIQUE NOT NULL,
  stage TEXT NOT NULL DEFAULT 'Intake',
  operational_state TEXT NOT NULL DEFAULT 'PROCESSING',
  entered_at TEXT NOT NULL,
  relationship_manager TEXT,
  credit_officer TEXT,
  evidence_version INTEGER NOT NULL DEFAULT 0,
  extracted_json TEXT NOT NULL DEFAULT '{}',
  issues_json TEXT NOT NULL DEFAULT '[]',
  fraud_json TEXT NOT NULL DEFAULT '{}',
  needed_by TEXT,
  updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS documents (
  id TEXT PRIMARY KEY,
  deal_id TEXT NOT NULL REFERENCES deals(id),
  path TEXT UNIQUE NOT NULL,
  filename TEXT NOT NULL,
  checksum TEXT NOT NULL,
  arrived_at TEXT NOT NULL,
  document_type TEXT NOT NULL,
  status TEXT NOT NULL,
  page_count INTEGER,
  producer TEXT,
  extracted_text TEXT NOT NULL DEFAULT '',
  facts_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id TEXT NOT NULL REFERENCES deals(id),
  stage TEXT NOT NULL,
  entered_at TEXT NOT NULL,
  exited_at TEXT,
  decision TEXT,
  why TEXT,
  waiting_on TEXT
);

CREATE TABLE IF NOT EXISTS human_requests (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id TEXT NOT NULL REFERENCES deals(id),
  type TEXT NOT NULL,
  recipient TEXT NOT NULL,
  requested_at TEXT NOT NULL,
  message TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'waiting',
  answer TEXT,
  answered_at TEXT,
  evidence_version INTEGER NOT NULL,
  invalidated_at TEXT
);

CREATE TABLE IF NOT EXISTS alerts (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  deal_id TEXT NOT NULL REFERENCES deals(id),
  at TEXT NOT NULL,
  stage TEXT NOT NULL,
  level TEXT NOT NULL,
  recipient TEXT NOT NULL,
  message TEXT NOT NULL,
  UNIQUE(deal_id, stage, level, recipient)
);

CREATE TABLE IF NOT EXISTS audit_events (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  at TEXT NOT NULL,
  deal_id TEXT,
  event_type TEXT NOT NULL,
  actor TEXT NOT NULL,
  payload_json TEXT NOT NULL DEFAULT '{}'
);

CREATE TABLE IF NOT EXISTS users (
  id TEXT PRIMARY KEY,
  email TEXT UNIQUE NOT NULL,
  password_hash TEXT,
  password_salt TEXT,
  password_iterations INTEGER,
  role TEXT NOT NULL,
  display_name TEXT NOT NULL,
  organization TEXT,
  created_at TEXT NOT NULL,
  disabled_at TEXT
);

CREATE TABLE IF NOT EXISTS sessions (
  token_hash TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  created_at TEXT NOT NULL,
  expires_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS borrower_applications (
  id TEXT PRIMARY KEY,
  user_id TEXT NOT NULL REFERENCES users(id),
  deal_id TEXT REFERENCES deals(id) ON DELETE SET NULL,
  status TEXT NOT NULL DEFAULT 'draft',
  current_step INTEGER NOT NULL DEFAULT 1,
  form_json TEXT NOT NULL DEFAULT '{}',
  score_json TEXT NOT NULL DEFAULT '{}',
  decision_json TEXT NOT NULL DEFAULT '{}',
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  submitted_at TEXT
);

CREATE TABLE IF NOT EXISTS fraud_patterns (
  signature TEXT PRIMARY KEY,
  name TEXT NOT NULL,
  features_json TEXT NOT NULL,
  occurrences INTEGER NOT NULL DEFAULT 1,
  first_seen TEXT NOT NULL,
  last_seen TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT 'learned'
);

CREATE INDEX IF NOT EXISTS idx_sessions_user ON sessions(user_id);
CREATE INDEX IF NOT EXISTS idx_applications_user ON borrower_applications(user_id);
"""


def utcish_now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    try:
        os.chmod(DB_PATH, 0o600)
    except OSError:
        pass
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


@contextmanager
def transaction():
    conn = connect()
    try:
        conn.execute("BEGIN")
        yield conn
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def initialize() -> None:
    with connect() as conn:
        conn.executescript(SCHEMA)
        conn.execute(
            "INSERT OR IGNORE INTO app_state(key, value) VALUES('sim_now', ?)",
            (SIM_START,),
        )
        conn.commit()


def get_state(conn: sqlite3.Connection, key: str, default=None):
    row = conn.execute("SELECT value FROM app_state WHERE key=?", (key,)).fetchone()
    return row["value"] if row else default


def set_state(conn: sqlite3.Connection, key: str, value: str) -> None:
    conn.execute(
        "INSERT INTO app_state(key, value) VALUES(?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value=excluded.value",
        (key, value),
    )


def event(conn, event_type: str, deal_id=None, actor="system", payload=None, at=None):
    conn.execute(
        "INSERT INTO audit_events(at, deal_id, event_type, actor, payload_json) VALUES(?,?,?,?,?)",
        (at or utcish_now(), deal_id, event_type, actor, json.dumps(payload or {})),
    )


def decode_row(row):
    if row is None:
        return None
    item = dict(row)
    for key in ("extracted_json", "issues_json", "fraud_json", "facts_json", "payload_json"):
        if key in item:
            raw = item.pop(key)
            item[key.removesuffix("_json")] = json.loads(raw or "{}")
    return item

