"""SQLite persistence layer for CitizenVoice NG."""
import hashlib
import hmac
import secrets
import sqlite3

from . import config

DB_PATH = config.DB_PATH

SCHEMA = """
CREATE TABLE IF NOT EXISTS agencies (
    id INTEGER PRIMARY KEY, code TEXT UNIQUE, name TEXT, category TEXT,
    contact_email TEXT, emergency_line TEXT
);
CREATE TABLE IF NOT EXISTS users (
    id INTEGER PRIMARY KEY, full_name TEXT, phone TEXT UNIQUE, email TEXT, lga TEXT,
    password_hash TEXT, role TEXT DEFAULT 'citizen', agency_id INTEGER,
    created_at TEXT DEFAULT (datetime('now','localtime')),
    status TEXT DEFAULT 'active', is_super INTEGER DEFAULT 0, approved_by INTEGER, approved_at TEXT
);
CREATE TABLE IF NOT EXISTS tokens (
    token TEXT PRIMARY KEY, user_id INTEGER, created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS complaints (
    id INTEGER PRIMARY KEY, ref TEXT UNIQUE, user_id INTEGER, text TEXT, category TEXT,
    agency_id INTEGER, sentiment TEXT, sentiment_conf REAL, urgency TEXT, score REAL,
    lga TEXT, community TEXT, status TEXT DEFAULT 'Submitted', analysis TEXT,
    sla_hours INTEGER, created_at TEXT, updated_at TEXT
);
CREATE TABLE IF NOT EXISTS status_history (
    id INTEGER PRIMARY KEY, complaint_id INTEGER, status TEXT, note TEXT, actor TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS dispatch_log (
    id INTEGER PRIMARY KEY, complaint_id INTEGER, agency_id INTEGER, channel TEXT,
    message TEXT, created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS messages (
    id INTEGER PRIMARY KEY, user_id INTEGER, sender TEXT, text TEXT, meta TEXT,
    created_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE TABLE IF NOT EXISTS conversations (
    user_id INTEGER PRIMARY KEY, state TEXT,
    updated_at TEXT DEFAULT (datetime('now','localtime'))
);
CREATE INDEX IF NOT EXISTS ix_complaints_user ON complaints(user_id);
CREATE INDEX IF NOT EXISTS ix_complaints_cluster ON complaints(category, lga, created_at);
CREATE INDEX IF NOT EXISTS ix_history_complaint ON status_history(complaint_id);
CREATE INDEX IF NOT EXISTS ix_dispatch_complaint ON dispatch_log(complaint_id);
CREATE INDEX IF NOT EXISTS ix_tokens_user ON tokens(user_id);
"""

AGENCIES = [
    ("NPF", "Nigeria Police Force, Nasarawa State Command", "Security", "pro.nasarawa@npf.gov.ng", "112 / 0803 000 0112"),
    ("NSFS", "Nasarawa State Fire Service", "Fire", "fireservice@nasarawastate.gov.ng", "112"),
    ("NASEMA", "Nasarawa State Emergency Management Agency", "Flood/Emergency", "info@nasema.gov.ng", "112"),
    ("MOH", "Nasarawa State Ministry of Health", "Health", "health@nasarawastate.gov.ng", "112"),
    ("AEDC", "Abuja Electricity Distribution Company, Nasarawa Region", "Electricity", "customercare@abujaelectricity.com", "0700 225 5233"),
    ("NSWB", "Nasarawa State Water Board", "Water", "waterboard@nasarawastate.gov.ng", ""),
    ("MOW", "Nasarawa State Ministry of Works and Transport", "Roads", "works@nasarawastate.gov.ng", ""),
    ("ICPC", "Independent Corrupt Practices Commission, Nasarawa Office", "Corruption", "info@icpc.gov.ng", ""),
    ("NASEPA", "Nasarawa State Environmental Protection Agency", "Environment", "nasepa@nasarawastate.gov.ng", ""),
    ("NSUBEB", "Nasarawa State Universal Basic Education Board", "Education", "subeb@nasarawastate.gov.ng", ""),
]

SLA_HOURS = {"High": 2, "Medium": 24, "Low": 72}
STATUSES = ("Submitted", "Forwarded", "Acknowledged", "In Progress", "Resolved", "Closed")


def connect(path=None):
    con = sqlite3.connect(path or DB_PATH, check_same_thread=False)
    con.row_factory = sqlite3.Row
    con.execute("PRAGMA foreign_keys=ON")
    con.execute("PRAGMA journal_mode=WAL")
    return con


def hash_pw(pw: str, salt: str = None) -> str:
    salt = salt or secrets.token_hex(8)
    h = hashlib.pbkdf2_hmac("sha256", pw.encode(), salt.encode(), 120_000).hex()
    return f"{salt}${h}"


def check_pw(pw: str, stored: str) -> bool:
    if not stored or "$" not in stored:
        return False
    salt = stored.split("$")[0]
    return hmac.compare_digest(hash_pw(pw, salt), stored)


# Officer accounts: role 'admin'. status is 'active', 'pending' (awaiting super-admin
# approval) or 'rejected'. Exactly one officer can be the super administrator.
OFFICER_STATUSES = ("active", "pending", "rejected")


def _migrate(con):
    """Bring databases created by older versions up to the current schema."""
    cols = {r["name"] for r in con.execute("PRAGMA table_info(users)")}
    for name, ddl in (("status", "TEXT DEFAULT 'active'"), ("is_super", "INTEGER DEFAULT 0"),
                      ("approved_by", "INTEGER"), ("approved_at", "TEXT")):
        if name not in cols:
            con.execute(f"ALTER TABLE users ADD COLUMN {name} {ddl}")
    con.execute("UPDATE users SET status='active' WHERE status IS NULL")
    # an older database's existing officer becomes the super administrator
    if not con.execute("SELECT 1 FROM users WHERE is_super=1").fetchone():
        first = con.execute("SELECT id FROM users WHERE role='admin' AND status='active' ORDER BY id LIMIT 1").fetchone()
        if first:
            con.execute("UPDATE users SET is_super=1 WHERE id=?", (first["id"],))
    # the database itself guarantees there is never more than one super administrator
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_single_super_admin ON users(is_super) WHERE is_super=1")
    con.execute("CREATE UNIQUE INDEX IF NOT EXISTS ux_users_email ON users(lower(email)) WHERE email<>''")


def has_super_admin(con) -> bool:
    return con.execute("SELECT 1 FROM users WHERE is_super=1 AND status='active'").fetchone() is not None


def init(path=None):
    """Create or upgrade the schema. No officer account is created here: the first officer
    registers through the officer console and becomes the super administrator."""
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(path)
    con.executescript(SCHEMA)
    _migrate(con)
    if not con.execute("SELECT 1 FROM agencies").fetchone():
        con.executemany("INSERT INTO agencies(code,name,category,contact_email,emergency_line) VALUES (?,?,?,?,?)", AGENCIES)
    con.commit()
    return con
