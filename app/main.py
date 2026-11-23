"""CitizenVoice NG - FastAPI application."""
import json
import logging
import secrets
from datetime import datetime, timedelta

from fastapi import FastAPI, HTTPException, Header, Depends, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, field_validator

from . import config, db
from .nlp import Models
from .agent import GrievanceAgent
from .dataset import LGAS

logging.basicConfig(level=config.LOG_LEVEL, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("citizenvoice")

STATIC = config.ROOT / "static"
VERSION = "2.0.0"
con = db.init()
models = Models()
agent = GrievanceAgent(con, models)
log.info("Environment: %s | database: %s | models: %s", config.ENV, config.DB_PATH, models.backend)
if config.IS_PRODUCTION and not config.DB_ON_VOLUME:
    log.warning("No persistent volume detected: complaints will be lost on the next deploy. "
                "Attach a Railway volume (or set CV_DB_PATH to persistent storage).")
# Interactive API docs are for development only.
_docs = {} if not config.IS_PRODUCTION else {"docs_url": None, "redoc_url": None, "openapi_url": None}
app = FastAPI(title="CitizenVoice NG API", version=VERSION, **_docs)
app.mount("/static", StaticFiles(directory=STATIC), name="static")


@app.exception_handler(RequestValidationError)
async def validation_error(request: Request, exc: RequestValidationError):
    """Return validation problems as one readable sentence the front end can display."""
    msgs = []
    for e in exc.errors():
        field = str(e["loc"][-1]).replace("_", " ")
        msgs.append(f"{field}: {e['msg'].removeprefix('Value error, ')}")
    return JSONResponse(status_code=400, content={"detail": "; ".join(msgs) or "Invalid request"})


@app.middleware("http")
async def security_headers(request: Request, call_next):
    resp = await call_next(request)
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["X-Frame-Options"] = "DENY"
    resp.headers["Referrer-Policy"] = "same-origin"
    # All scripts and styles are external files under /static, so inline code can be refused outright.
    resp.headers["Content-Security-Policy"] = (
        "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
        "connect-src 'self'; object-src 'none'; base-uri 'self'; form-action 'self'; frame-ancestors 'none'")
    if config.IS_PRODUCTION:
        resp.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
    return resp


# ------------------------------------------------------------------ request bodies
class RegisterIn(BaseModel):
    full_name: str = Field(min_length=2, max_length=80)
    phone: str = Field(min_length=7, max_length=20)
    email: str = Field(default="", max_length=120)
    lga: str
    password: str = Field(max_length=128)

    @field_validator("full_name", "phone", "email")
    @classmethod
    def strip(cls, v):
        return v.strip()

    @field_validator("phone")
    @classmethod
    def phone_digits(cls, v):
        if not v.lstrip("+").isdigit():
            raise ValueError("Phone number must contain digits only")
        return v

    @field_validator("lga")
    @classmethod
    def known_lga(cls, v):
        if v not in LGAS:
            raise ValueError("Unknown Local Government Area")
        return v


class LoginIn(BaseModel):
    phone: str = Field(max_length=120)
    password: str = Field(max_length=128)


class ChatIn(BaseModel):
    message: str = Field(max_length=config.MAX_MESSAGE_CHARS)


class StatusIn(BaseModel):
    status: str
    note: str = Field(default="", max_length=500)


# ------------------------------------------------------------------ auth
def _bearer(authorization: str) -> str:
    return authorization[7:] if authorization.startswith("Bearer ") else ""


def _user_from_token(tok: str):
    if not tok:
        return None
    since = (datetime.now() - timedelta(hours=config.TOKEN_TTL_HOURS)).strftime("%Y-%m-%d %H:%M:%S")
    row = con.execute("SELECT u.* FROM tokens t JOIN users u ON u.id=t.user_id WHERE t.token=? AND t.created_at>=?",
                      (tok, since)).fetchone()
    return dict(row) if row else None


async def current_user(authorization: str = Header(default="")):
    user = _user_from_token(_bearer(authorization))
    if not user:
        raise HTTPException(401, "Please log in")
    return user


async def optional_user(authorization: str = Header(default="")):
    return _user_from_token(_bearer(authorization))


async def admin_user(user=Depends(current_user)):
    if user["role"] != "admin":
        raise HTTPException(403, "Administrators only")
    return user


def _token(uid):
    tok = secrets.token_urlsafe(24)
    con.execute("INSERT INTO tokens(token,user_id) VALUES (?,?)", (tok, uid))
    con.commit()
    return tok


# ------------------------------------------------------------------ public + citizen API
@app.get("/api/health")
async def health():
    con.execute("SELECT 1").fetchone()
    return {"status": "ok", "version": VERSION, "environment": config.ENV, "model_backend": models.backend}


@app.post("/api/register")
async def register(body: RegisterIn):
    if len(body.password) < 6:
        raise HTTPException(400, "Password must be at least 6 characters")
    if con.execute("SELECT 1 FROM users WHERE phone=?", (body.phone,)).fetchone():
        raise HTTPException(400, "Phone number already registered")
    cur = con.execute("INSERT INTO users(full_name,phone,email,lga,password_hash) VALUES (?,?,?,?,?)",
                      (body.full_name, body.phone, body.email, body.lga, db.hash_pw(body.password)))
    con.commit()
    log.info("New citizen account %s", cur.lastrowid)
    return {"token": _token(cur.lastrowid), "name": body.full_name, "role": "citizen"}


@app.post("/api/login")
async def login(body: LoginIn):
    ident = body.phone.strip()
    row = con.execute("SELECT * FROM users WHERE phone=? OR (email<>'' AND email=?)", (ident, ident)).fetchone()
    if not row or not db.check_pw(body.password, row["password_hash"]):
        raise HTTPException(401, "Invalid phone number or password")
    return {"token": _token(row["id"]), "name": row["full_name"], "role": row["role"]}


@app.post("/api/logout")
async def logout(authorization: str = Header(default="")):
    con.execute("DELETE FROM tokens WHERE token=?", (_bearer(authorization),))
    con.commit()
    return {"ok": True}


@app.get("/api/me")
async def me(user=Depends(current_user)):
    return {k: user[k] for k in ("id", "full_name", "phone", "email", "lga", "role")}


@app.get("/api/lgas")
async def lgas():
    return LGAS


@app.post("/api/chat")
async def chat(body: ChatIn, user=Depends(current_user)):
    if not body.message.strip():
        raise HTTPException(400, "Empty message")
    try:
        return agent.step(user, body.message)
    except Exception:
        log.exception("Agent failure for user %s", user["id"])
        raise HTTPException(500, "Sorry, something went wrong while processing your message. Please try again.")


@app.get("/api/my/complaints")
async def my_complaints(user=Depends(current_user)):
    rows = con.execute("SELECT ref,category,urgency,status,created_at FROM complaints WHERE user_id=? "
                       "ORDER BY id DESC", (user["id"],)).fetchall()
    return [dict(r) for r in rows]


@app.get("/api/track/{ref}")
async def track(ref: str, user=Depends(optional_user)):
    """Public tracking. The complaint text and community are shown only to its owner and officers."""
    row = con.execute("SELECT c.*, a.name AS agency, a.code AS agency_code FROM complaints c "
                      "JOIN agencies a ON a.id=c.agency_id WHERE ref=?", (ref.strip().upper(),)).fetchone()
    if not row:
        raise HTTPException(404, "Reference not found")
    hist = con.execute("SELECT status,note,actor,created_at FROM status_history WHERE complaint_id=? ORDER BY id",
                       (row["id"],)).fetchall()
    d = {k: row[k] for k in ("ref", "category", "urgency", "status", "lga", "sla_hours", "created_at",
                             "updated_at", "agency", "agency_code")}
    owner = bool(user) and (user["role"] == "admin" or user["id"] == row["user_id"])
    d["owner_view"] = owner
    if owner:
        d["text"], d["community"] = row["text"], row["community"]
    d["history"] = [dict(h) for h in hist]
    return d


# ------------------------------------------------------------------ officer API
PRIORITY_ORDER = "CASE urgency WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END"


@app.get("/api/admin/complaints")
async def admin_list(urgency: str = "", status: str = "", category: str = "", q: str = "", user=Depends(admin_user)):
    sql = ("SELECT c.id,c.ref,c.text,c.category,c.sentiment,c.urgency,c.score,c.lga,c.status,c.created_at,"
           "c.sla_hours,a.code AS agency FROM complaints c JOIN agencies a ON a.id=c.agency_id WHERE 1=1")
    args = []
    for col, val in (("urgency", urgency), ("status", status), ("category", category)):
        if val:
            sql += f" AND c.{col}=?"
            args.append(val)
    if q:
        sql += " AND (c.text LIKE ? OR c.ref LIKE ?)"
        args += [f"%{q}%", f"%{q}%"]
    sql += f" ORDER BY CASE WHEN c.status IN ('Resolved','Closed') THEN 1 ELSE 0 END, {PRIORITY_ORDER}, c.score DESC, c.created_at"
    rows = [dict(r) for r in con.execute(sql, args).fetchall()]
    now = datetime.now()
    for r in rows:
        age_h = (now - datetime.strptime(r["created_at"], "%Y-%m-%d %H:%M:%S")).total_seconds() / 3600
        r["age_hours"] = round(age_h, 1)
        r["overdue"] = r["status"] in ("Submitted", "Forwarded") and age_h > r["sla_hours"]
    return rows


@app.get("/api/admin/complaints/{cid}")
async def admin_detail(cid: int, user=Depends(admin_user)):
    row = con.execute("SELECT c.*, a.name AS agency, a.code AS agency_code, u.full_name AS citizen, u.phone "
                      "FROM complaints c JOIN agencies a ON a.id=c.agency_id JOIN users u ON u.id=c.user_id "
                      "WHERE c.id=?", (cid,)).fetchone()
    if not row:
        raise HTTPException(404, "Complaint not found")
    d = dict(row)
    d["analysis"] = json.loads(d["analysis"] or "{}")
    d["history"] = [dict(h) for h in con.execute("SELECT * FROM status_history WHERE complaint_id=? ORDER BY id", (cid,))]
    d["dispatch"] = [dict(h) for h in con.execute("SELECT * FROM dispatch_log WHERE complaint_id=?", (cid,))]
    return d


@app.post("/api/admin/complaints/{cid}/status")
async def admin_status(cid: int, body: StatusIn, user=Depends(admin_user)):
    if body.status not in ("Forwarded", "Acknowledged", "In Progress", "Resolved", "Closed"):
        raise HTTPException(400, "Invalid status")
    if not con.execute("SELECT 1 FROM complaints WHERE id=?", (cid,)).fetchone():
        raise HTTPException(404, "Complaint not found")
    now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    con.execute("UPDATE complaints SET status=?, updated_at=? WHERE id=?", (body.status, now, cid))
    con.execute("INSERT INTO status_history(complaint_id,status,note,actor,created_at) VALUES (?,?,?,?,?)",
                (cid, body.status, body.note.strip() or f"Status changed to {body.status}", user["full_name"], now))
    con.commit()
    log.info("Complaint %s -> %s by user %s", cid, body.status, user["id"])
    return {"ok": True}


@app.get("/api/admin/stats")
async def admin_stats(user=Depends(admin_user)):
    q = lambda s, a=(): con.execute(s, a).fetchone()[0]
    open_ = "status NOT IN ('Resolved','Closed')"
    by = lambda col: [dict(r) for r in con.execute(
        f"SELECT {col} AS k, COUNT(*) AS n FROM complaints GROUP BY {col} ORDER BY n DESC")]
    daily = [dict(r) for r in con.execute(
        "SELECT substr(created_at,1,10) AS d, SUM(urgency='High') AS high, SUM(urgency='Medium') AS medium, "
        "SUM(urgency='Low') AS low FROM complaints GROUP BY d ORDER BY d DESC LIMIT 14")][::-1]
    resolved = [dict(r) for r in con.execute(
        "SELECT c.urgency, AVG((julianday(h.created_at)-julianday(c.created_at))*24) AS hrs FROM complaints c "
        "JOIN status_history h ON h.complaint_id=c.id AND h.status='Acknowledged' GROUP BY c.urgency "
        "ORDER BY CASE c.urgency WHEN 'High' THEN 0 WHEN 'Medium' THEN 1 ELSE 2 END")]
    return {
        "total": q("SELECT COUNT(*) FROM complaints"),
        "open": q(f"SELECT COUNT(*) FROM complaints WHERE {open_}"),
        "high_open": q(f"SELECT COUNT(*) FROM complaints WHERE urgency='High' AND {open_}"),
        "resolved": q("SELECT COUNT(*) FROM complaints WHERE status IN ('Resolved','Closed')"),
        "negative_pct": round(100 * (q("SELECT AVG(sentiment='Negative') FROM complaints") or 0), 1),
        "by_category": by("category"), "by_urgency": by("urgency"), "by_sentiment": by("sentiment"),
        "by_lga": by("lga"), "by_status": by("status"), "daily": daily, "ack_hours": resolved,
    }


@app.get("/api/admin/dispatch")
async def admin_dispatch(user=Depends(admin_user)):
    rows = con.execute("SELECT d.*, a.code, a.name, c.ref, c.urgency FROM dispatch_log d JOIN agencies a ON "
                       "a.id=d.agency_id JOIN complaints c ON c.id=d.complaint_id ORDER BY d.created_at DESC LIMIT 40").fetchall()
    agencies = con.execute("SELECT a.code,a.name,a.category, COUNT(c.id) AS total, "
                           "SUM(c.urgency='High') AS high, SUM(c.status NOT IN ('Resolved','Closed')) AS open "
                           "FROM agencies a LEFT JOIN complaints c ON c.agency_id=a.id GROUP BY a.id ORDER BY total DESC").fetchall()
    return {"log": [dict(r) for r in rows], "agencies": [dict(r) for r in agencies]}


@app.get("/api/admin/model")
async def admin_model(user=Depends(admin_user)):
    out = {"selected": models.meta, "backend": models.backend, "thresholds": models.thresholds,
           "weights": models.weights, "tasks": {}, "triage": {}, "finetune": None}
    p = config.REPORT_DIR / "metrics.json"
    if p.exists():
        m = json.loads(p.read_text())
        out["tasks"] = {k: {a: v for a, v in t.items() if a not in ("report", "confusion")}
                        for k, t in m["tasks"].items()}
        out.update(triage=m["triage"], n_train=m["n_train"], n_test=m["n_test"])
    f = config.REPORT_DIR / "finetune.json"
    if f.exists():
        ft = json.loads(f.read_text())
        out["finetune"] = {k: ft[k] for k in ("budget_minutes", "total_minutes", "n_dev", "classical", "weights",
                                              "thresholds", "test", "triage", "triage_ms_per_message")}
        tr = ft["transformer"]
        out["finetune"]["transformer"] = {k: tr[k] for k in ("base_model", "epochs", "steps", "best_epoch",
                                                             "train_minutes")}
    return out


# ------------------------------------------------------------------ pages
@app.get("/")
async def index():
    return FileResponse(STATIC / "index.html")


@app.get("/chat")
async def chat_page():
    return FileResponse(STATIC / "chat.html")


@app.get("/track")
async def track_page():
    return FileResponse(STATIC / "track.html")


@app.get("/admin")
async def admin_page():
    return FileResponse(STATIC / "admin.html")
