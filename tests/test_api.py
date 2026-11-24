"""End-to-end API tests (accounts, chatbot flow, tracking, officer console, security)."""
from datetime import datetime, timedelta


def chat(client, headers, msg):
    r = client.post("/api/chat", json={"message": msg}, headers=headers)
    assert r.status_code == 200, r.text
    return r.json()


# ------------------------------------------------------------------ accounts
def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200 and r.json()["status"] == "ok"


def test_register_duplicate_and_bad_login(client, citizen):
    _, phone = citizen
    dup = client.post("/api/register", json={"full_name": "Dup", "phone": phone, "lga": "Keffi", "password": "abc123"})
    assert dup.status_code == 400 and "already registered" in dup.json()["detail"]
    bad = client.post("/api/login", json={"phone": phone, "password": "wrong"})
    assert bad.status_code == 401


def test_register_validation_is_readable(client):
    r = client.post("/api/register", json={"full_name": "X Y", "phone": "08012345678", "lga": "Atlantis",
                                           "password": "abc123"})
    assert r.status_code == 400
    assert "Local Government Area" in r.json()["detail"]
    r = client.post("/api/register", json={"full_name": "X Y", "phone": "0801234567", "lga": "Keffi",
                                           "password": "123"})
    assert r.status_code == 400 and "6 characters" in r.json()["detail"]


def test_logout_revokes_token(client, citizen):
    h, _ = citizen
    assert client.get("/api/me", headers=h).status_code == 200
    assert client.post("/api/logout", headers=h).status_code == 200
    assert client.get("/api/me", headers=h).status_code == 401


def test_expired_token_rejected(client, citizen):
    from app.main import con
    h, _ = citizen
    old = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d %H:%M:%S")
    con.execute("UPDATE tokens SET created_at=? WHERE token=?", (old, h["Authorization"][7:]))
    con.commit()
    assert client.get("/api/me", headers=h).status_code == 401


def test_message_length_limit(client, citizen):
    h, _ = citizen
    r = client.post("/api/chat", json={"message": "a" * 5000}, headers=h)
    assert r.status_code == 400


# ------------------------------------------------------------------ chatbot flow (functional test cases)
def test_full_reporting_flow(client, citizen, admin_headers):
    h, _ = citizen
    assert chat(client, h, "Hello")["action"] == "GREET"
    assert chat(client, h, "no water")["action"] == "ASK_DETAILS"
    chat(client, h, "cancel")
    d = chat(client, h, "The community borehole has been broken for weeks and people now trek far for water. We are tired.")
    assert d["action"] == "ASK_LOCATION"
    d = chat(client, h, "Akwanga")
    assert d["action"] == "CONFIRM" and d["analysis"]["category"] == "Water"
    d = chat(client, h, "yes")
    assert d["action"] == "DISPATCH_TO_AGENCY" and d["ref"].startswith("CV-")
    ref = d["ref"]

    d = chat(client, h, ref)
    assert d["action"] == "REPORT_STATUS"

    # owner sees the text on the tracking page; the public does not
    pub = client.get("/api/track/" + ref).json()
    assert pub["owner_view"] is False and "text" not in pub and "user_id" not in pub
    assert len(pub["history"]) >= 2
    own = client.get("/api/track/" + ref, headers=h).json()
    assert own["owner_view"] is True and "borehole" in own["text"]
    assert client.get("/api/my/complaints", headers=h).json()[0]["ref"] == ref


def test_emergency_is_escalated(client, citizen):
    h, _ = citizen
    d = chat(client, h, "Gunmen are attacking our village in Doma right now and two people are injured. Please come now!")
    assert d["action"] == "ESCALATE_EMERGENCY"
    assert d["analysis"]["urgency"] == "High" and d["analysis"]["category"] == "Security"
    d = chat(client, h, "near the primary school")
    assert d["action"] == "DISPATCH_TO_AGENCY"


def test_routine_request_is_low_priority(client, citizen):
    h, _ = citizen
    d = chat(client, h, "Please put a speed bump in front of the primary school on Jos road in Lafia. "
                        "Thank you for the good work.")
    assert d["analysis"]["category"] == "Roads"
    assert d["analysis"]["urgency"] == "Low"


def test_conversation_survives_agent_restart(client, citizen):
    """A half-finished report is stored in the database, not only in memory."""
    from app import main
    h, _ = citizen
    assert chat(client, h, "The transformer in our area has been faulty for two months and we have no light.")[
        "action"] == "ASK_LOCATION"
    main.agent.sessions.clear()          # simulate a server restart
    d = chat(client, h, "Karu")
    assert d["action"] == "CONFIRM" and d["analysis"]["category"] == "Electricity"


# ------------------------------------------------------------------ officer console
def test_citizen_cannot_use_officer_api(client, citizen):
    h, _ = citizen
    assert client.get("/api/admin/complaints", headers=h).status_code == 403


def test_queue_order_and_status_update(client, admin_headers):
    q = client.get("/api/admin/complaints", headers=admin_headers).json()
    open_q = [x for x in q if x["status"] not in ("Resolved", "Closed")]
    rank = {"High": 0, "Medium": 1, "Low": 2}
    assert all(rank[a["urgency"]] <= rank[b["urgency"]] for a, b in zip(open_q, open_q[1:]))
    cid = q[0]["id"]
    r = client.post(f"/api/admin/complaints/{cid}/status", json={"status": "Acknowledged", "note": "Team notified"},
                    headers=admin_headers)
    assert r.status_code == 200
    hist = client.get(f"/api/admin/complaints/{cid}", headers=admin_headers).json()["history"]
    assert hist[-1]["status"] == "Acknowledged" and hist[-1]["note"] == "Team notified"


def test_status_update_unknown_complaint(client, admin_headers):
    r = client.post("/api/admin/complaints/999999/status", json={"status": "Closed"}, headers=admin_headers)
    assert r.status_code == 404
    r = client.post("/api/admin/complaints/1/status", json={"status": "Deleted"}, headers=admin_headers)
    assert r.status_code == 400


def test_admin_dashboards(client, admin_headers):
    s = client.get("/api/admin/stats", headers=admin_headers).json()
    assert s["total"] >= 1
    assert client.get("/api/admin/dispatch", headers=admin_headers).status_code == 200
    m = client.get("/api/admin/model", headers=admin_headers).json()
    assert set(m["selected"]) == {"sentiment", "urgency", "category"}


def test_security_headers(client):
    r = client.get("/")
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["X-Frame-Options"] == "DENY"


def test_content_security_policy(client):
    csp = client.get("/").headers["Content-Security-Policy"]
    assert "script-src 'self'" in csp and "unsafe-inline" not in csp


def test_pages_have_no_inline_code():
    """Scripts and styles must live in static/js and static/css (required by the CSP)."""
    import re
    from app.config import ROOT
    for page in (ROOT / "static").glob("*.html"):
        html = page.read_text()
        assert not re.search(r"<script(?![^>]*\bsrc=)", html), f"inline <script> in {page.name}"
        assert "<style" not in html, f"<style> block in {page.name}"
        assert not re.search(r"\sstyle=", html), f"style attribute in {page.name}"
        assert not re.search(r"\son[a-z]+=", html), f"inline event handler in {page.name}"


def test_static_assets_served(client):
    for path in ("/static/css/base.css", "/static/js/common.js", "/static/js/chat.js", "/static/img/favicon.svg"):
        assert client.get(path).status_code == 200, path


# ------------------------------------------------------------------ officer accounts
def _officer(n):
    return {"full_name": f"Officer {n}", "email": f"officer{n}@citizenvoice.ng", "phone": f"0700000{n:04d}",
            "password": "OfficerPass1"}


def test_officer_registration_requires_super_admin_approval(client, admin_headers):
    # the super administrator already exists (admin_headers fixture), so a new officer waits
    assert client.get("/api/officers/setup").json() == {"has_super_admin": True}
    reg = client.post("/api/officers/register", json=_officer(1))
    assert reg.status_code == 200 and reg.json()["status"] == "pending" and "token" not in reg.json()

    login = {"phone": "officer1@citizenvoice.ng", "password": "OfficerPass1"}
    r = client.post("/api/login", json=login)
    assert r.status_code == 403 and "awaiting approval" in r.json()["detail"]

    officers = client.get("/api/admin/officers", headers=admin_headers).json()
    pending = next(o for o in officers if o["email"] == "officer1@citizenvoice.ng")
    assert pending["status"] == "pending"
    assert client.post(f"/api/admin/officers/{pending['id']}", json={"action": "approve"},
                       headers=admin_headers).status_code == 200

    r = client.post("/api/login", json=login)
    assert r.status_code == 200 and r.json()["role"] == "admin" and r.json()["is_super"] is False
    officer = {"Authorization": "Bearer " + r.json()["token"]}
    assert client.get("/api/admin/complaints", headers=officer).status_code == 200
    # only the super administrator manages officers
    assert client.get("/api/admin/officers", headers=officer).status_code == 403


def test_rejected_officer_cannot_sign_in(client, admin_headers):
    client.post("/api/officers/register", json=_officer(2))
    officers = client.get("/api/admin/officers", headers=admin_headers).json()
    oid = next(o["id"] for o in officers if o["email"] == "officer2@citizenvoice.ng")
    client.post(f"/api/admin/officers/{oid}", json={"action": "reject"}, headers=admin_headers)
    r = client.post("/api/login", json={"phone": "officer2@citizenvoice.ng", "password": "OfficerPass1"})
    assert r.status_code == 403 and "not active" in r.json()["detail"]


def test_super_admin_cannot_be_changed_and_is_unique(client, admin_headers):
    me = client.get("/api/me", headers=admin_headers).json()
    assert me["is_super"] is True
    r = client.post(f"/api/admin/officers/{me['id']}", json={"action": "reject"}, headers=admin_headers)
    assert r.status_code == 400
    supers = [o for o in client.get("/api/admin/officers", headers=admin_headers).json() if o["is_super"]]
    assert len(supers) == 1


def test_officer_registration_validation(client):
    bad = {**_officer(3), "password": "short"}
    assert client.post("/api/officers/register", json=bad).status_code == 400
    bad = {**_officer(3), "email": "not-an-email"}
    r = client.post("/api/officers/register", json=bad)
    assert r.status_code == 400 and "email" in r.json()["detail"].lower()
    client.post("/api/officers/register", json=_officer(4))
    dup = client.post("/api/officers/register", json={**_officer(5), "email": "OFFICER4@citizenvoice.ng"})
    assert dup.status_code == 400 and "already registered" in dup.json()["detail"]
