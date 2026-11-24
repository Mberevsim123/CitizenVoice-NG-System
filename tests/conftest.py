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


SUPER_ADMIN = {"full_name": "Super Officer", "email": "super@citizenvoice.ng", "phone": "08000000001",
               "password": "SuperSecret1"}


@pytest.fixture(scope="session")
def admin_headers(client):
    """The first officer to register on the fresh test database: the super administrator."""
    r = client.post("/api/officers/register", json=SUPER_ADMIN)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["status"] == "active" and body["is_super"] is True
    return {"Authorization": "Bearer " + body["token"]}


@pytest.fixture
def citizen(client):
    """A newly registered citizen; returns (headers, phone)."""
    import secrets
    phone = "0812" + str(secrets.randbelow(10 ** 7)).zfill(7)
    r = client.post("/api/register", json={"full_name": "Test Citizen", "phone": phone, "lga": "Keffi",
                                           "password": "abc123"})
    assert r.status_code == 200, r.text
    return {"Authorization": "Bearer " + r.json()["token"]}, phone
