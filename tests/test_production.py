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


def test_production_refuses_default_admin_password():
    with tempfile.TemporaryDirectory() as tmp:
        r = run("from app import db; db.init()", CV_ENV="production", CV_DB_PATH=f"{tmp}/a.db")
        assert r.returncode != 0 and "CV_ADMIN_PASSWORD" in r.stderr


def test_production_starts_with_admin_password():
    with tempfile.TemporaryDirectory() as tmp:
        r = run("from app import db; con = db.init(); print(con.execute(\"SELECT email FROM users WHERE role='admin'\").fetchone()[0])",
                CV_ENV="production", CV_DB_PATH=f"{tmp}/a.db", CV_ADMIN_PASSWORD="a-long-test-secret")
        assert r.returncode == 0, r.stderr
        assert r.stdout.strip() == "admin@citizenvoice.ng"
