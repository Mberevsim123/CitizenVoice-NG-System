"""Production configuration safeguards (run in subprocesses with a clean environment)."""
import os
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def run(code: str, **env) -> subprocess.CompletedProcess:
    base = {k: v for k, v in os.environ.items() if not k.startswith(("CV_", "RAILWAY_"))}
    return subprocess.run([sys.executable, "-c", code], cwd=ROOT, env={**base, **env},
                          capture_output=True, text=True, timeout=120)


def test_railway_defaults_to_production_and_volume():
    with tempfile.TemporaryDirectory() as vol:
        r = run("from app import config; print(config.ENV, config.DB_PATH)",
                RAILWAY_ENVIRONMENT_NAME="production", RAILWAY_VOLUME_MOUNT_PATH=vol)
        assert r.returncode == 0, r.stderr
        env, path = r.stdout.split()
        assert env == "production" and path == str(Path(vol) / "citizenvoice.db")


def test_production_starts_without_creating_an_officer():
    """No officer account is created automatically: the first officer registers in the console."""
    with tempfile.TemporaryDirectory() as tmp:
        r = run("from app import db; con = db.init(); "
                "print(con.execute(\"SELECT COUNT(*) FROM users WHERE role='admin'\").fetchone()[0], db.has_super_admin(con))",
                CV_ENV="production", CV_DB_PATH=f"{tmp}/a.db")
        assert r.returncode == 0, r.stderr
        assert r.stdout.split() == ["0", "False"]


def test_seed_refused_in_production():
    with tempfile.TemporaryDirectory() as tmp:
        r = run("from app import seed; seed.main()", CV_ENV="production", CV_DB_PATH=f"{tmp}/a.db")
        assert r.returncode != 0 and "disabled in production" in (r.stderr + r.stdout)


def test_old_database_admin_becomes_super_admin():
    """A database from an earlier version keeps its officer, promoted to super administrator."""
    import sqlite3
    with tempfile.TemporaryDirectory() as tmp:
        old = sqlite3.connect(f"{tmp}/old.db")
        old.executescript("CREATE TABLE users (id INTEGER PRIMARY KEY, full_name TEXT, phone TEXT UNIQUE, email TEXT,"
                          " lga TEXT, password_hash TEXT, role TEXT DEFAULT 'citizen', agency_id INTEGER, created_at TEXT);"
                          "INSERT INTO users(full_name,phone,email,role) VALUES ('Old Officer','080','old@x.ng','admin');")
        old.commit()
        old.close()
        r = run("from app import db; con = db.init(); "
                "print(*con.execute(\"SELECT status, is_super FROM users WHERE role='admin'\").fetchone())",
                CV_DB_PATH=f"{tmp}/old.db")
        assert r.returncode == 0, r.stderr
        assert r.stdout.split() == ["active", "1"]
