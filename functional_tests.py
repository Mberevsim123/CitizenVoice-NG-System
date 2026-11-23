"""Functional test cases for CitizenVoice NG (run against a live server)."""
import requests, json, sys
B = sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8010"
res = []
def rec(i, desc, exp, ok, actual): res.append((i, desc, exp, actual, "Pass" if ok else "Fail"))
r = requests.post(B+"/api/register", json={"full_name":"Test Citizen","phone":"08120000001","lga":"Keffi","password":"abc123"})
rec("TC01","Register a new citizen account","Account created, token issued", r.ok and "token" in r.json(), f"HTTP {r.status_code}")
r2 = requests.post(B+"/api/register", json={"full_name":"Dup","phone":"08120000001","lga":"Keffi","password":"abc123"})
rec("TC02","Register with an existing phone number","Request rejected", r2.status_code==400, r2.json().get("detail"))
r3 = requests.post(B+"/api/login", json={"phone":"08120000001","password":"wrong"})
rec("TC03","Login with wrong password","Access denied", r3.status_code==401, r3.json().get("detail"))
tok = requests.post(B+"/api/login", json={"phone":"08120000001","password":"abc123"}).json()["token"]
H={"Authorization":"Bearer "+tok}
chat=lambda m: requests.post(B+"/api/chat", json={"message":m}, headers=H).json()
d=chat("Hello"); rec("TC04","Greet the chatbot","Agent greets citizen", d["action"]=="GREET", d["action"])
d=chat("no water"); rec("TC05","Send a too-short complaint","Agent asks for more details", d["action"]=="ASK_DETAILS", d["action"])
chat("cancel")
d=chat("The community borehole has been broken for weeks and people now trek far for water. We are tired.")
rec("TC06","Complaint without a location","Agent asks for the LGA", d["action"]=="ASK_LOCATION", d["action"])
d=chat("Akwanga"); rec("TC07","Provide LGA","Agent shows summary for confirmation", d["action"]=="CONFIRM", f'{d["action"]}: {d["analysis"]["category"]}, {d["analysis"]["urgency"]}')
d=chat("yes"); ref=d["ref"]; rec("TC08","Confirm submission","Complaint saved, reference issued, routed to Water Board", d["action"]=="DISPATCH_TO_AGENCY" and ref is not None, f"{ref}")
d=chat("Gunmen are attacking our village in Doma right now and two people are injured. Please come now!")
rec("TC09","Report a life-threatening incident","Immediate HIGH filing and emergency escalation", d["action"]=="ESCALATE_EMERGENCY" and d["analysis"]["urgency"]=="High", f'{d["action"]}, {d["analysis"]["category"]}, score {d["analysis"]["score"]}')
chat("near the primary school")
d=chat("Please put a speed bump in front of the primary school on Jos road in Lafia. Thank you for the good work.")
d=chat("yes") if d["action"]=="CONFIRM" else d
rec("TC10","Report a routine request","Classified LOW and routed to Ministry of Works", d.get("analysis",{}) and d["analysis"]["urgency"]=="Low" and d["analysis"]["category"]=="Roads", f'{d["analysis"]["category"]}, {d["analysis"]["urgency"]}')
d=chat(ref); rec("TC11","Track complaint by reference in chat","Current status returned", d["action"]=="REPORT_STATUS", d["messages"][0][:60])
t=requests.get(B+"/api/track/"+ref); rec("TC12","Track complaint on public page","Timeline returned", t.ok and len(t.json()["history"])>=2, f'{len(t.json()["history"])} timeline events')
a=requests.get(B+"/api/admin/complaints", headers=H); rec("TC13","Citizen opens officer endpoint","Access forbidden", a.status_code==403, f"HTTP {a.status_code}")
at=requests.post(B+"/api/login", json={"phone":"admin@citizenvoice.ng","password":"Admin@123"}).json()["token"]; AH={"Authorization":"Bearer "+at}
q=requests.get(B+"/api/admin/complaints", headers=AH).json()
open_q=[x for x in q if x["status"] not in ("Resolved","Closed")]
order_ok=all({"High":0,"Medium":1,"Low":2}[open_q[i]["urgency"]]<={"High":0,"Medium":1,"Low":2}[open_q[i+1]["urgency"]] for i in range(len(open_q)-1))
rec("TC14","Officer views triage queue","Open complaints ordered High, Medium, Low", order_ok, f"{len(open_q)} open complaints ordered")
cid=q[0]["id"]; u=requests.post(B+f"/api/admin/complaints/{cid}/status", json={"status":"Acknowledged","note":"Team notified"}, headers=AH)
h=requests.get(B+f"/api/admin/complaints/{cid}", headers=AH).json()["history"][-1]
rec("TC15","Officer updates complaint status","Status saved and added to timeline", u.ok and h["status"]=="Acknowledged", h["status"])
json.dump(res, open("reports/functional_tests.json","w"), indent=1)
for x in res: print(x)
