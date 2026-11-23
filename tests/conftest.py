"""Test fixtures: every test session runs against a fresh, temporary database."""
import os
import sys
import tempfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
_tmp = tempfile.mkdtemp(prefix="citizenvoice-test-")
os.environ["CV_DB_PATH"] = str(Path(_tmp) / "test.db")


@pytest.fixture(scope="session")
def client():
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app) as c:
        yield c


@pytest.fixture(scope="session")
def admin_headers(client):
    r = client.post("/api/login", json={"phone": "admin@citizenvoice.ng", "password": "Admin@123"})
    assert r.status_code == 200
    return {"Authorization": "Bearer " + r.json()["token"]}


@pytest.fixture
def citizen(client):
    """A newly registered citizen; returns (headers, phone)."""
    import secrets
    phone = "0812" + str(secrets.randbelow(10 ** 7)).zfill(7)
    r = client.post("/api/register", json={"full_name": "Test Citizen", "phone": phone, "lga": "Keffi",
                                           "password": "abc123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, phone
