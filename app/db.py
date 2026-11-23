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
    created_at TEXT DEFAULT (datetime('now','localtime'))
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


def init(path=None):
    path = path or DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    con = connect(path)
    con.executescript(SCHEMA)
    if not con.execute("SELECT 1 FROM agencies").fetchone():
        con.executemany("INSERT INTO agencies(code,name,category,contact_email,emergency_line) VALUES (?,?,?,?,?)", AGENCIES)
    if not con.execute("SELECT 1 FROM users WHERE role='admin'").fetchone():
        if config.IS_PRODUCTION and len(config.ADMIN_PASSWORD) < config.MIN_ADMIN_PASSWORD_LENGTH:
            con.close()
            raise RuntimeError(
                "Production start-up refused: set the CV_ADMIN_PASSWORD environment variable "
                f"(at least {config.MIN_ADMIN_PASSWORD_LENGTH} characters) so the first officer "
                "account does not use a public default password.")
        con.execute("INSERT INTO users(full_name,phone,email,lga,password_hash,role) VALUES (?,?,?,?,?,?)",
                    ("Grievance Desk Officer", "08000000000", config.ADMIN_EMAIL, "Lafia",
                     hash_pw(config.ADMIN_PASSWORD), "admin"))
    con.commit()
    return con
