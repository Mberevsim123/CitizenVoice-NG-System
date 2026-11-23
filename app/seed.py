"""Populates the database with sample citizens and complaints for demonstration."""
import random
from datetime import datetime, timedelta
import pandas as pd

from . import db, config
from .nlp import Models, triage
from .agent import GrievanceAgent, ConversationState

NAMES = ["Amina Yusuf", "Emeka Okafor", "Danjuma Audu", "Grace Ogah", "Ibrahim Musa", "Blessing Ade",
         "Yakubu Danladi", "Fatima Bello", "Samuel Agbo", "Hauwa Umar", "John Alu", "Mercy Akpan",
         "Abdullahi Sani", "Ruth Gyang", "Tanko Adamu", "Gbari Akpalaakpo"]


def main():
    for suffix in ("", "-wal", "-shm"):
        f = db.DB_PATH.with_name(db.DB_PATH.name + suffix)
        if f.exists():
            f.unlink()
    con = db.init()
    rng = random.Random(11)
    models = Models()
    agent = GrievanceAgent(con, models)
    users = []
    for i, n in enumerate(NAMES):
        lga = rng.choice(["Keffi", "Lafia", "Akwanga", "Karu", "Nasarawa", "Doma", "Kokona"])
        cur = con.execute("INSERT INTO users(full_name,phone,email,lga,password_hash) VALUES (?,?,?,?,?)",
                          (n, f"0803{1000000 + i * 7919:07d}", "", lga, db.hash_pw("Citizen@123")))
        users.append(dict(con.execute("SELECT * FROM users WHERE id=?", (cur.lastrowid,)).fetchone()))
    con.commit()
    df = pd.read_csv(config.ROOT / "data" / "grievances_test.csv").sample(150, random_state=3)
    now = datetime.now()
    for _, r in df.iterrows():
        u = rng.choice(users)
        created = now - timedelta(days=rng.uniform(0.05, 13.8))
        st = ConversationState(text=r.text, lga=r.lga)
        st.analysis = triage(r.text, models)
        ref = agent._create(u, st, created_at=created.strftime("%Y-%m-%d %H:%M:%S"))
        cid = int(ref.split("-")[-1])
        urg = st.analysis["urgency"]
        age_h = (now - created).total_seconds() / 3600
        ack_h = {"High": rng.uniform(0.3, 2.6), "Medium": rng.uniform(3, 30), "Low": rng.uniform(8, 80)}[urg]
        steps = []
        if age_h > ack_h:
            steps.append(("Acknowledged", ack_h, "Complaint acknowledged by agency desk"))
            prog = ack_h + rng.uniform(1, 30)
            if age_h > prog:
                steps.append(("In Progress", prog, "Field team assigned"))
                res = prog + (rng.uniform(3, 30) if urg == "High" else rng.uniform(6, 90))
                if age_h > res and rng.random() < (0.92 if urg == "High" else 0.8):
                    steps.append(("Resolved", res, "Issue resolved and confirmed with citizen"))
        for s, h, note in steps:
            t = (created + timedelta(hours=h)).strftime("%Y-%m-%d %H:%M:%S")
            con.execute("INSERT INTO status_history(complaint_id,status,note,actor,created_at) VALUES (?,?,?,?,?)",
                        (cid, s, note, "Agency desk officer", t))
            con.execute("UPDATE complaints SET status=?, updated_at=? WHERE id=?", (s, t, cid))
    con.commit()
    print("seeded", con.execute("SELECT COUNT(*) FROM complaints").fetchone()[0])


if __name__ == "__main__":
    main()
