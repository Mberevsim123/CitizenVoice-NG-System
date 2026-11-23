"""
CitizenVoice grievance agent.

The chatbot is built as a goal-based intelligent agent (Russell & Norvig, 2021):

    PERCEIVE  ->  UPDATE STATE  ->  DECIDE  ->  ACT

Percepts (what the agent senses from its environment)
    - the citizen's message text
    - the conversation state (what has already been collected for this complaint)
    - the citizen profile (registered LGA)
    - the clock (night-time reports, SLA deadlines)
    - the complaint database (similar reports from the same LGA in the last 24 hours)

Actions (what the agent can do to its environment)
    GREET, ASK_DETAILS, ASK_LOCATION, CONFIRM, FILE_COMPLAINT, ESCALATE_EMERGENCY,
    DISPATCH_TO_AGENCY, REPORT_STATUS, ANSWER_HELP, CANCEL
"""
import json
import re
from dataclasses import dataclass, field, asdict
from datetime import datetime, timedelta

from .nlp import triage, Models
from .db import SLA_HOURS
from .dataset import LGAS

GREETING_RE = re.compile(r"^\s*(hi|hello|hey|good (morning|afternoon|evening)|greetings)\b[\s!.]*$", re.I)
TRACK_RE = re.compile(r"\b(CV-\d{4}-\d{5})\b", re.I)
CANCEL_RE = re.compile(r"^\s*(cancel|stop|start over|restart)\b", re.I)
YES_RE = re.compile(r"^\s*(yes|y|submit|confirm|ok|okay|correct|go ahead)\b", re.I)
NO_RE = re.compile(r"^\s*(no|n|edit|change|wrong)\b", re.I)
HELP_RE = re.compile(r"\b(help|what can you do|how does this work)\b", re.I)

EMERGENCY_LINES = ("National Emergency Number: 112 (toll-free) | "
                   "Nasarawa Police Control Room | Nasarawa State Fire Service")


@dataclass
class ConversationState:
    stage: str = "idle"          # idle -> collecting -> confirming
    text: str = ""
    lga: str = ""
    community: str = ""
    analysis: dict = field(default_factory=dict)
    emergency_filed: str = ""    # ref of an emergency complaint already filed in this turn


@dataclass
class Percept:
    text: str
    user: dict
    now: datetime
    state: ConversationState
    lga_mentioned: str = ""
    cluster_count: int = 0


class GrievanceAgent:
    def __init__(self, con, models: Models):
        self.con = con
        self.models = models
        self.sessions: dict[int, ConversationState] = {}

    # ------------------------------------------------------------------ state persistence
    def _load_state(self, uid: int) -> ConversationState:
        row = self.con.execute("SELECT state FROM conversations WHERE user_id=?", (uid,)).fetchone()
        if row:
            try:
                return ConversationState(**json.loads(row["state"]))
            except (TypeError, ValueError):
                pass
        return ConversationState()

    def _save_state(self, uid: int):
        state = self.sessions.get(uid)
        if state is None:
            return
        self.con.execute("INSERT INTO conversations(user_id,state,updated_at) VALUES (?,?,datetime('now','localtime')) "
                         "ON CONFLICT(user_id) DO UPDATE SET state=excluded.state, updated_at=excluded.updated_at",
                         (uid, json.dumps(asdict(state))))
        self.con.commit()

    # ------------------------------------------------------------------ PERCEIVE
    def perceive(self, user: dict, text: str) -> Percept:
        if user["id"] not in self.sessions:
            self.sessions[user["id"]] = self._load_state(user["id"])
        state = self.sessions[user["id"]]
        lga = next((l for l in sorted(LGAS, key=len, reverse=True) if l.lower() in text.lower()), "")
        return Percept(text=text.strip(), user=user, now=datetime.now(), state=state, lga_mentioned=lga)

    def similar_reports(self, category: str, lga: str) -> int:
        since = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S")
        row = self.con.execute("SELECT COUNT(*) FROM complaints WHERE category=? AND lga=? AND created_at>=?",
                               (category, lga, since)).fetchone()
        return row[0]

    # ------------------------------------------------------------------ DECIDE + ACT
    def step(self, user: dict, text: str) -> dict:
        """One perceive-decide-act cycle. The conversation state is saved after every turn."""
        try:
            return self._step(user, text)
        finally:
            self._save_state(user["id"])

    def _step(self, user: dict, text: str) -> dict:
        p = self.perceive(user, text)
        s = p.state
        self._log(user["id"], "citizen", text)

        if CANCEL_RE.match(p.text):
            self.sessions[user["id"]] = ConversationState()
            return self._reply(user, ["Okay, I have cleared that report. You can describe a new complaint anytime."],
                               action="CANCEL")
        m = TRACK_RE.search(p.text)
        if m:
            return self._status(user, m.group(1).upper())
        if s.stage == "idle" and GREETING_RE.match(p.text):
            return self._reply(user, [f"Hello {user['full_name'].split()[0]}, I am CitizenVoice, the Nasarawa State "
                                      "grievance assistant. Tell me what is wrong in your community and where it is "
                                      "happening. I will analyse it, set its priority and send it to the right agency."],
                               action="GREET")
        if s.stage == "idle" and HELP_RE.search(p.text) and len(p.text.split()) < 8:
            return self._reply(user, ["You can report problems such as insecurity, fire, flooding, health services, "
                                      "electricity, water, roads, corruption, sanitation or schools. Describe the "
                                      "problem in your own words. To check a complaint, type its reference, "
                                      "for example CV-2026-00012."], action="ANSWER_HELP")

        row = None
        if s.stage == "emergency_followup":
            row = self.con.execute("SELECT id FROM complaints WHERE ref=?", (s.emergency_filed,)).fetchone()
            if not row:   # the emergency record no longer exists: start a fresh conversation
                s = p.state = self.sessions[user["id"]] = ConversationState()

        if s.stage == "emergency_followup":
            ref = s.emergency_filed
            now = p.now.strftime("%Y-%m-%d %H:%M:%S")
            cid = row[0]
            self.con.execute("UPDATE complaints SET community=?, text=text || ' [Location detail: ' || ? || ']', "
                             "updated_at=? WHERE id=?", (p.text[:120], p.text[:120], now, cid))
            self.con.execute("INSERT INTO status_history(complaint_id,status,note,actor,created_at) VALUES (?,?,?,?,?)",
                             (cid, "Forwarded", f"Location detail added by citizen: {p.text[:120]}",
                              "CitizenVoice Agent", now))
            self.con.commit()
            self.sessions[user["id"]] = ConversationState()
            return self._reply(user, [f"Thank you. I have added that location detail to {ref} and passed it on to "
                                      "the responders. You can type the reference number anytime for updates."],
                               action="DISPATCH_TO_AGENCY", ref=ref)

        if s.stage == "confirming":
            if YES_RE.match(p.text):
                return self._file(user, s)
            if NO_RE.match(p.text):
                s.stage = "collecting"
                return self._reply(user, ["No problem. Please describe the complaint again with the correct details."],
                                   action="ASK_DETAILS")
            if len(p.text.split()) >= 6:
                # a fresh, full description replaces the unsubmitted draft
                s.text, s.lga, s.stage = p.text, "", "collecting"
            else:
                s.text += " " + p.text
        elif s.stage == "collecting" and not s.lga and p.lga_mentioned and len(p.text.split()) <= 6:
            pass  # pure location answer
        elif s.stage == "collecting":
            alone = triage(p.text, self.models)
            if alone["urgency"] == "High" and alone["critical_terms"]:
                s.text, s.lga = p.text, ""   # a new emergency overrides the draft
            else:
                s.text += " " + p.text
        else:
            s.text = p.text
            s.stage = "collecting"

        if p.lga_mentioned:
            s.lga = p.lga_mentioned

        # analyse the accumulated complaint (sentiment + category + urgency)
        a = triage(s.text, self.models)
        lga_for_cluster = s.lga or user.get("lga") or ""
        cluster = self.similar_reports(a["category"], lga_for_cluster) if lga_for_cluster else 0
        if cluster:
            a = triage(s.text, self.models, cluster_count=cluster)
        hour = p.now.hour
        a["night_report"] = hour >= 21 or hour < 5
        s.analysis = a

        # Rule 1: life-threatening -> file and dispatch immediately, then ask for anything missing
        if a["urgency"] == "High" and a["critical_terms"] and not s.emergency_filed:
            if not s.lga:
                s.lga = user.get("lga") or ""
            ref = self._create(user, s, auto=True)
            s.emergency_filed = ref
            agency = self._agency(a["category"])
            msgs = [f"This looks like an EMERGENCY ({a['category']}). I have filed it immediately as {ref} with "
                    f"HIGH priority and alerted the {agency['name']}.",
                    f"If anyone is in danger, call now: {EMERGENCY_LINES}.",
                    "Please stay safe. If you can, tell me the exact community or landmark so responders can find it."]
            self.sessions[user["id"]] = ConversationState(stage="emergency_followup", emergency_filed=ref)
            return self._reply(user, msgs, action="ESCALATE_EMERGENCY", ref=ref, analysis=a)

        # Rule 2: not enough detail
        if len(s.text.split()) < 6:
            return self._reply(user, ["Please tell me a little more: what exactly happened, and since when?"],
                               action="ASK_DETAILS", analysis=a)
        # Rule 3: location missing
        if not s.lga:
            return self._reply(user, ["Which Local Government Area is this happening in? You can also add the "
                                      "community or a nearby landmark."], action="ASK_LOCATION", analysis=a,
                               quick=[l for l in LGAS[:8]])
        # Rule 4: confirm before filing
        s.stage = "confirming"
        agency = self._agency(a["category"])
        art = "an" if a["category"][0] in "AEIOU" else "a"
        msgs = [f"Here is what I understood: {art} {a['category'].lower()} complaint in {s.lga} LGA. "
                f"Tone detected: {a['sentiment'].lower()}. Priority: {a['urgency'].upper()} "
                f"(score {a['score']}/100). It will go to the {agency['name']} with a response target of "
                f"{SLA_HOURS[a['urgency']]} hours.",
                "Shall I submit it? Reply YES to submit or NO to edit."]
        if cluster:
            msgs.insert(1, f"Note: {cluster} similar report(s) came from {s.lga} in the last 24 hours, "
                           "so the priority has been raised.")
        return self._reply(user, msgs, action="CONFIRM", analysis=a, quick=["Yes, submit", "No, edit"])

    # ------------------------------------------------------------------ actuators
    def _agency(self, category):
        return dict(self.con.execute("SELECT * FROM agencies WHERE category=?", (category,)).fetchone())

    def _create(self, user, s: ConversationState, auto=False, created_at=None, status="Submitted"):
        a = s.analysis
        agency = self._agency(a["category"])
        now = created_at or datetime.now().strftime("%Y-%m-%d %H:%M:%S")
        cur = self.con.execute(
            "INSERT INTO complaints(user_id,text,category,agency_id,sentiment,sentiment_conf,urgency,score,lga,"
            "community,status,analysis,sla_hours,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (user["id"], s.text.strip(), a["category"], agency["id"], a["sentiment"], a["sentiment_confidence"],
             a["urgency"], a["score"], s.lga, s.community, status, json.dumps(a), SLA_HOURS[a["urgency"]], now, now))
        cid = cur.lastrowid
        ref = f"CV-{now[:4]}-{cid:05d}"
        self.con.execute("UPDATE complaints SET ref=? WHERE id=?", (ref, cid))
        self.con.execute("INSERT INTO status_history(complaint_id,status,note,actor,created_at) VALUES (?,?,?,?,?)",
                         (cid, "Submitted", "Filed automatically (emergency)" if auto else "Filed by citizen via chatbot",
                          "CitizenVoice Agent", now))
        channel = "SMS + Email + Dashboard alert" if a["urgency"] == "High" else "Email + Dashboard"
        self.con.execute("INSERT INTO dispatch_log(complaint_id,agency_id,channel,message,created_at) VALUES (?,?,?,?,?)",
                         (cid, agency["id"], channel,
                          f"[{a['urgency'].upper()}] {ref} {a['category']} in {s.lga}: {s.text.strip()[:140]}", now))
        self.con.execute("INSERT INTO status_history(complaint_id,status,note,actor,created_at) VALUES (?,?,?,?,?)",
                         (cid, "Forwarded", f"Routed to {agency['code']} via {channel}", "CitizenVoice Agent", now))
        self.con.execute("UPDATE complaints SET status='Forwarded' WHERE id=? AND status='Submitted'", (cid,))
        self.con.commit()
        return ref

    def _file(self, user, s):
        ref = self._create(user, s)
        a = s.analysis
        agency = self._agency(a["category"])
        self.sessions[user["id"]] = ConversationState()
        return self._reply(user, [f"Your complaint has been submitted. Reference number: {ref}.",
                                  f"It has been forwarded to the {agency['name']} with {a['urgency'].upper()} priority. "
                                  f"Expected first response within {SLA_HOURS[a['urgency']]} hours.",
                                  f"Type {ref} at any time to check its status."],
                           action="DISPATCH_TO_AGENCY", ref=ref, analysis=a)

    def _status(self, user, ref):
        row = self.con.execute("SELECT c.*, a.name AS agency FROM complaints c JOIN agencies a ON a.id=c.agency_id "
                               "WHERE ref=?", (ref,)).fetchone()
        if not row:
            return self._reply(user, [f"I could not find any complaint with reference {ref}."], action="REPORT_STATUS")
        last = self.con.execute("SELECT note FROM status_history WHERE complaint_id=? ORDER BY id DESC LIMIT 1",
                                (row["id"],)).fetchone()
        return self._reply(user, [f"{ref}: {row['status']}. Priority {row['urgency'].upper()}, handled by {row['agency']}. "
                                  f"Latest update: {last['note'] if last else '-'}"], action="REPORT_STATUS")

    def _log(self, uid, sender, text, meta=None):
        self.con.execute("INSERT INTO messages(user_id,sender,text,meta) VALUES (?,?,?,?)",
                         (uid, sender, text, json.dumps(meta) if meta else None))
        self.con.commit()

    def _reply(self, user, msgs, action, ref=None, analysis=None, quick=None):
        for m in msgs:
            self._log(user["id"], "agent", m, {"action": action})
        return {"messages": msgs, "action": action, "ref": ref, "analysis": analysis, "quick_replies": quick or []}
