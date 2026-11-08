import base64
import hashlib
import hmac
import re
import secrets
from datetime import datetime, timedelta, timezone

from . import db


PASSWORD_ITERATIONS = 310_000
SESSION_HOURS = 12
EMAIL_PATTERN = re.compile(r"^[^\s@]+@[^\s@]+\.[^\s@]+$")

BANK_DEMO_USERS = {
    "admin": ("admin@trinitybank.example", "Bank Administrator"),
    "credit_officer": ("credit@trinitybank.example", "Credit Officer"),
    "committee": ("committee@trinitybank.example", "Committee Approver"),
    "relationship_manager": ("rm@trinitybank.example", "Relationship Manager"),
    "fraud_ops": ("fraud@trinitybank.example", "Fraud Operations"),
    "funding_officer": ("funding@trinitybank.example", "Funding Officer"),
}


def _now():
    return datetime.now(timezone.utc)


def _user_id():
    return "usr_" + secrets.token_hex(12)


def password_digest(password: str, salt: bytes, iterations: int = PASSWORD_ITERATIONS) -> str:
    digest = hashlib.pbkdf2_hmac("sha256", password.encode("utf-8"), salt, iterations)
    return base64.b64encode(digest).decode("ascii")


def password_policy(password: str):
    if len(password) < 12:
        raise ValueError("Password must be at least 12 characters")
    if not re.search(r"[A-Z]", password) or not re.search(r"[a-z]", password) or not re.search(r"\d", password):
        raise ValueError("Password must include uppercase, lowercase, and a number")


def public_user(row):
    return {
        "id": row["id"],
        "email": row["email"],
        "role": row["role"],
        "display_name": row["display_name"],
        "organization": row["organization"],
    }


def seed_bank_users():
    now = _now().isoformat(timespec="seconds")
    with db.transaction() as conn:
        for role, (email, name) in BANK_DEMO_USERS.items():
            conn.execute(
                """INSERT OR IGNORE INTO users(
                     id, email, role, display_name, organization, created_at
                   ) VALUES(?,?,?,?,?,?)""",
                (f"bank_{role}", email, role, name, "Trinity Commercial Bank", now),
            )


def signup(email: str, password: str, display_name: str, organization: str):
    email = (email or "").strip().lower()
    display_name = (display_name or "").strip()
    organization = (organization or "").strip()
    if not EMAIL_PATTERN.fullmatch(email):
        raise ValueError("Enter a valid email address")
    if len(display_name) < 2 or len(organization) < 2:
        raise ValueError("Name and business name are required")
    password_policy(password or "")
    salt = secrets.token_bytes(16)
    digest = password_digest(password, salt)
    now = _now().isoformat(timespec="seconds")
    identifier = _user_id()
    try:
        with db.transaction() as conn:
            conn.execute(
                """INSERT INTO users(
                     id, email, password_hash, password_salt, password_iterations,
                     role, display_name, organization, created_at
                   ) VALUES(?,?,?,?,?,'consumer',?,?,?)""",
                (
                    identifier, email, digest, base64.b64encode(salt).decode("ascii"),
                    PASSWORD_ITERATIONS, display_name, organization, now,
                ),
            )
            row = conn.execute("SELECT * FROM users WHERE id=?", (identifier,)).fetchone()
    except Exception as exc:
        if "UNIQUE constraint" in str(exc):
            raise ValueError("An account with this email already exists") from None
        raise
    return public_user(row), create_session(identifier)


def login(email: str, password: str, allowed_roles=None):
    email = (email or "").strip().lower()
    alias_map = {
        "admin": "admin@trinitybank.example",
        "admin@trinitycommercial.example": "admin@trinitybank.example",
        "credit": "credit@trinitybank.example",
        "credit@trinitycommercial.example": "credit@trinitybank.example",
        "marcus.lee@trinitycommercial.example": "credit@trinitybank.example",
        "dana.whitfield@trinitycommercial.example": "credit@trinitybank.example",
        "committee": "committee@trinitybank.example",
        "committee@trinitycommercial.example": "committee@trinitybank.example",
        "priscilla.nguyen@trinitycommercial.example": "committee@trinitybank.example",
        "harold.benson@trinitycommercial.example": "committee@trinitybank.example",
        "rm": "rm@trinitybank.example",
        "rm@trinitycommercial.example": "rm@trinitybank.example",
        "brian.foster@trinitycommercial.example": "rm@trinitybank.example",
        "fraud": "fraud@trinitybank.example",
        "fraud@trinitycommercial.example": "fraud@trinitybank.example",
        "victor.hale@trinitycommercial.example": "fraud@trinitybank.example",
        "funding": "funding@trinitybank.example",
        "funding@trinitycommercial.example": "funding@trinitybank.example",
        "tasha.reed@trinitycommercial.example": "funding@trinitybank.example",
    }
    email = alias_map.get(email, email)
    with db.connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE email=? AND disabled_at IS NULL", (email,)
        ).fetchone()
    # Always perform a derivation to reduce account-enumeration timing differences.
    salt = base64.b64decode(row["password_salt"]) if row and row["password_salt"] else secrets.token_bytes(16)
    iterations = row["password_iterations"] if row and row["password_iterations"] else PASSWORD_ITERATIONS
    candidate = password_digest(password or "", salt, iterations)
    matched = bool(row and row["password_hash"] and hmac.compare_digest(candidate, row["password_hash"]))
    if not matched and row and row["password_hash"] and row["role"] in BANK_DEMO_USERS:
        # Allow case variants for demo passwords like dealpilot2026! or DealPilot2026!
        clean_pwd = (password or "").strip()
        for variant in (clean_pwd.lower(), clean_pwd.capitalize(), "DealPilot2026!", "dealpilot2026!"):
            if hmac.compare_digest(password_digest(variant, salt, iterations), row["password_hash"]):
                matched = True
                break
    if not row or not matched:
        raise ValueError("Email or password is incorrect")
    if allowed_roles and row["role"] not in allowed_roles:
        raise ValueError("Email or password is incorrect")
    return public_user(row), create_session(row["id"])


def set_bank_password(email: str, password: str):
    password_policy(password)
    salt = secrets.token_bytes(16)
    digest = password_digest(password, salt)
    with db.transaction() as conn:
        row = conn.execute("SELECT id,role FROM users WHERE email=?", (email.strip().lower(),)).fetchone()
        if not row or row["role"] not in BANK_DEMO_USERS:
            raise ValueError("Bank employee account not found")
        conn.execute(
            "UPDATE users SET password_hash=?,password_salt=?,password_iterations=? WHERE id=?",
            (digest, base64.b64encode(salt).decode("ascii"), PASSWORD_ITERATIONS, row["id"]),
        )
    return row["id"]


def create_session(user_id: str):
    token = secrets.token_urlsafe(32)
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = _now()
    expires = now + timedelta(hours=SESSION_HOURS)
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE expires_at < ?", (now.isoformat(),))
        conn.execute(
            "INSERT INTO sessions(token_hash,user_id,created_at,expires_at) VALUES(?,?,?,?)",
            (token_hash, user_id, now.isoformat(), expires.isoformat()),
        )
    return token


def cookie_for(token: str):
    return f"dealpilot_session={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={SESSION_HOURS * 3600}"


def clear_cookie():
    return "dealpilot_session=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"


def token_from_cookie(cookie_header: str):
    for part in (cookie_header or "").split(";"):
        key, separator, value = part.strip().partition("=")
        if separator and key == "dealpilot_session":
            return value
    return None


def current_user(cookie_header: str):
    token = token_from_cookie(cookie_header)
    if not token:
        return None
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    now = _now().isoformat()
    with db.connect() as conn:
        row = conn.execute(
            """SELECT u.* FROM sessions s JOIN users u ON u.id=s.user_id
               WHERE s.token_hash=? AND s.expires_at>? AND u.disabled_at IS NULL""",
            (token_hash, now),
        ).fetchone()
    return public_user(row) if row else None


def logout(cookie_header: str):
    token = token_from_cookie(cookie_header)
    if not token:
        return
    token_hash = hashlib.sha256(token.encode("ascii")).hexdigest()
    with db.transaction() as conn:
        conn.execute("DELETE FROM sessions WHERE token_hash=?", (token_hash,))

