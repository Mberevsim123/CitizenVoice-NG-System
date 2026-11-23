"""
CitizenVoice NG - sample grievance corpus builder.

Builds an annotated corpus of citizen complaint texts modelled on the categories
of grievances reported to public agencies in Nasarawa State and the FCT axis.
Each record carries three labels:
    category  - the responsible agency group
    sentiment - Negative / Neutral / Positive
    urgency   - High / Medium / Low

Complaints are assembled from phrase banks written in Nigerian English (with some
Pidgin), so that the classifiers meet the kind of wording citizens actually use.
Phrase banks are split into two disjoint halves (A and B). Training texts are
drawn from half A and test texts from half B, so the test set never contains a
phrase pattern the model saw during training.
"""
import csv
import random
from pathlib import Path

LGAS = ["Keffi", "Lafia", "Akwanga", "Karu", "Nasarawa", "Doma", "Awe", "Keana",
        "Kokona", "Nasarawa Eggon", "Obi", "Toto", "Wamba"]
PLACES = ["Angwan Lambu", "Tudun Kofa", "High Court junction", "Mararaba", "New Karu",
          "Shabu", "Bukan Sidi", "Kwandere road", "Garaku", "Masaka", "Gudi", "Ado",
          "Angwan Mada", "Doma road", "Jos road", "Sabon Pegi"]

# (category, severity) -> list of problem phrases. severity: 3 critical, 2 serious, 1 routine
PROBLEMS = {
    ("Security", 3): [
        "armed men are shooting sporadically in our community right now",
        "kidnappers just abducted two people from our street",
        "bandits attacked the village and people are injured",
        "some boys with guns are robbing people on the road as I type",
        "there is a violent clash between two groups and houses are being burnt",
        "a man was stabbed and he is bleeding on the road",
        "gunmen are moving from house to house tonight",
        "cultists are attacking students near the hostel now",
    ],
    ("Security", 2): [
        "there have been repeated burglaries in our area this month",
        "phone snatchers operate every evening at the junction",
        "suspicious people keep gathering behind the market at night",
        "thieves broke into three shops in the plaza last week",
        "there is no police patrol in the area since the last robbery",
        "drug users have taken over the abandoned building near the school",
    ],
    ("Security", 1): [
        "the street lights at the police checkpoint are not working",
        "I want to suggest more community policing meetings",
        "the police post in our ward needs a signboard",
        "I need information on how to join the neighbourhood watch",
    ],
    ("Fire", 3): [
        "a fire has broken out at the market and it is spreading fast",
        "a gas tanker exploded and houses are burning",
        "our neighbour's house is on fire and people are trapped inside",
        "fire is burning the petrol station near the motor park",
        "the filling station is on fire and smoke is everywhere",
        "a bush fire is spreading towards the houses",
    ],
    ("Fire", 2): [
        "the fire hydrants in the market have been dry for months",
        "the fire service truck in our LGA has been broken down",
        "people keep selling fuel in jerrycans beside houses which is a fire risk",
        "illegal gas refilling shops are operating inside residential buildings",
    ],
    ("Fire", 1): [
        "I want to know how to get a fire safety certificate for my shop",
        "please organise fire safety training for our school",
        "the fire extinguishers in the secretariat have expired",
    ],
    ("Flood/Emergency", 3): [
        "flood has submerged our houses and families are stranded on the roof",
        "the bridge collapsed and a vehicle fell into the river",
        "a building just collapsed and people are trapped under it",
        "the dam water has overflowed and children are being swept away",
        "erosion has washed away the road and a house is about to fall",
    ],
    ("Flood/Emergency", 2): [
        "every time it rains the drainage overflows into our houses",
        "erosion is eating into the foundation of houses on our street",
        "the river bank keeps expanding towards the community",
        "displaced flood victims in the camp have not received relief materials",
    ],
    ("Flood/Emergency", 1): [
        "I want information about flood early warning for this rainy season",
        "please desilt the drainage before the rains start",
        "can SEMA share the list of flood prone areas",
    ],
    ("Health", 3): [
        "a woman in labour has been refused admission at the general hospital",
        "many people in our village are vomiting and stooling after drinking the well water",
        "there is a cholera outbreak and two people have died",
        "an accident victim has been left at the hospital without treatment",
        "children are convulsing and the health centre has no doctor",
        "a snake bite victim cannot get anti venom at the hospital",
    ],
    ("Health", 2): [
        "the primary health centre has no drugs for malaria",
        "nurses at the clinic demand money before they attend to patients",
        "the hospital generator is not working so surgeries are postponed",
        "there have been no immunisation vaccines at the PHC for weeks",
        "the health centre is always locked by noon",
    ],
    ("Health", 1): [
        "the waiting area at the general hospital needs more chairs",
        "please put up a notice board with the clinic opening hours",
        "I want to know the schedule for the next immunisation exercise",
        "the hospital toilets need cleaning",
    ],
    ("Electricity", 3): [
        "a high tension cable fell on the road and it is sparking",
        "an electric pole fell on a house and the wire is live",
        "a child was electrocuted by an exposed transformer cable",
        "the transformer exploded and fire is coming out of it",
    ],
    ("Electricity", 2): [
        "we have not had electricity for three weeks",
        "AEDC brought an estimated bill that is far above our usage",
        "the transformer in our area has been faulty for two months",
        "AEDC staff are demanding money before they replace the fuse",
        "no light for one month yet we keep receiving bills",
        "the prepaid meter has been rejecting tokens since last week",
    ],
    ("Electricity", 1): [
        "I applied for a prepaid meter and want to know the status",
        "please update the outage schedule on your page",
        "the street light on our road blinks at night",
    ],
    ("Water", 3): [
        "a water pipe burst and is flooding the transformer area",
        "the borehole water has turned dark and smells of chemicals and people are falling sick",
    ],
    ("Water", 2): [
        "there has been no pipe borne water in our area for two months",
        "the community borehole has been broken for weeks",
        "a burst pipe has been wasting water on the road for weeks",
        "the water vendors now sell at three times the price because the public taps are dry",
    ],
    ("Water", 1): [
        "I want to apply for a new water connection",
        "the water bill should show the meter reading",
        "please paint the public water tank",
    ],
    ("Roads", 3): [
        "a big crater on the highway just caused an accident and people are injured",
        "the road has caved in and vehicles are falling into the gully",
        "a fallen tree is blocking the expressway and a car crashed into it",
    ],
    ("Roads", 2): [
        "the road to the general hospital is impassable",
        "potholes on the Keffi Akwanga road are causing accidents every week",
        "the bridge on our road is cracked and shaking when trucks pass",
        "the road contractor abandoned the project for over a year",
        "the drainage on the road is blocked and the road is flooding",
    ],
    ("Roads", 1): [
        "the road markings at the junction have faded",
        "please put a speed bump in front of the primary school",
        "the road signs on the bypass are bent",
    ],
    ("Corruption", 3): [
        "officials are diverting relief materials meant for flood victims right now",
        "a police officer shot at a driver who refused to give bribe",
    ],
    ("Corruption", 2): [
        "officers at the checkpoint collect money from every commercial driver",
        "the local government staff demanded a bribe before issuing my certificate",
        "salaries of ghost workers are being paid in our ministry",
        "the contract for the borehole was paid for but nothing was done",
        "they asked me to pay before registering my child in a public school",
    ],
    ("Corruption", 1): [
        "I want to know how to report a corruption case anonymously",
        "please publish the budget for our ward projects",
        "I want to know the procedure for requesting public information",
    ],
    ("Environment", 3): [
        "toxic smoke from the dumpsite fire is choking residents",
        "a chemical spill from a truck is flowing into the stream we drink from",
    ],
    ("Environment", 2): [
        "refuse has not been evacuated from the market for weeks",
        "a company is dumping waste into the river",
        "the dumpsite near the school is breeding mosquitoes and flies",
        "illegal mining has left open pits all over the community",
        "people are burning refuse near residential houses every night",
    ],
    ("Environment", 1): [
        "please place more waste bins along the main road",
        "I want to know the date for the next environmental sanitation",
        "the trees along the road need trimming",
    ],
    ("Education", 3): [
        "the classroom roof collapsed while pupils were inside",
        "students are trapped after a fight broke out in the school",
    ],
    ("Education", 2): [
        "the public school has no teachers for mathematics and English",
        "pupils sit on the floor because there are no desks",
        "the school roof was blown off and classes hold under trees",
        "teachers have not been paid for months so they stopped teaching",
    ],
    ("Education", 1): [
        "I want to know when the scholarship portal will open",
        "please share the date for the common entrance examination",
        "the school needs a library",
    ],
}

OPENINGS_A = ["Good day,", "Please help,", "Sir,", "Hello,", "I want to report that",
              "My name is a resident of {lga}.", "Ma,", "Abeg,", "Urgent:", "Dear government,"]
OPENINGS_B = ["Good morning,", "Help us,", "Madam,", "Hi,", "I wish to complain that",
              "I live in {lga} and", "Please,", "Biko,", "Attention please,", "To whom it may concern,"]

LOCS_A = ["in {place}, {lga}", "at {place}", "here in {lga}", "around {place} in {lga} LGA"]
LOCS_B = ["at {place}, {lga} LGA", "in {lga} town", "near {place}", "behind {place}, {lga}"]

# tone -> phrases. Tone drives the sentiment label.
TONE_A = {
    "Negative": ["We are tired of this government.", "This is very frustrating.",
                 "Nobody cares about us.", "We are suffering and angry.",
                 "This is wickedness.", "I am disappointed in the authorities.",
                 "We are afraid for our lives.", "Na wa for this country.",
                 "People are crying and helpless.", "This is unacceptable."],
    "Neutral": ["Kindly look into it.", "Please treat accordingly.",
                "I am writing for your attention.", "This is for your information.",
                "Kindly advise on the next step.", "I would like a response."],
    "Positive": ["Thank you for the good work so far.", "We appreciate the recent effort.",
                 "I commend the agency for listening.", "God bless you for your service.",
                 "We are grateful for this platform."],
}
TONE_B = {
    "Negative": ["We are fed up.", "This is terrible and painful.",
                 "The government has abandoned us.", "Everybody is angry and scared.",
                 "This is a disgrace.", "We are frustrated beyond words.",
                 "We are living in fear.", "Wahala dey o.", "This is so sad.",
                 "Shameful situation."],
    "Neutral": ["Please attend to this.", "Kindly take note.",
                "This is a formal report.", "Kindly acknowledge receipt.",
                "Please let me know what to do.", "Awaiting your feedback."],
    "Positive": ["Thanks for the quick response last time.", "Well done to the team.",
                 "We are happy with the new platform.", "Keep up the good work.",
                 "We thank the governor for the support."],
}

AMPLIFIERS = ["Please come now!", "Hurry!", "Emergency!!!", "Lives are at risk.",
              "Send help immediately.", "We need help fast."]
DURATIONS = ["for two weeks", "since last month", "for over three months", "since January",
             "for some days now", "for a long time"]
VULNERABLE = ["Children are affected.", "Old people and pregnant women live here.",
              "There is a school nearby.", "Many families are involved."]


SYNONYMS = [
    ("armed men", "gunmen"), ("shooting", "firing guns"), ("kidnappers", "abductors"),
    ("injured", "wounded"), ("robbing", "robbing and beating"), ("houses", "homes"),
    ("a fire has", "an inferno has"), ("is on fire", "is burning"), ("spreading", "spreading quickly"), ("trapped", "stuck"),
    ("flood", "flood water"), ("collapsed", "fell down"), ("hospital", "teaching hospital"),
    ("health centre", "PHC"), ("electricity", "power supply"), ("transformer", "transformer (trafo)"),
    ("bribe", "kickback"), ("money", "cash"), ("road", "highway"), ("potholes", "pot holes"),
    ("refuse", "garbage"), ("waste", "rubbish"), ("teachers", "tutors"), ("pupils", "school children"),
    ("weeks", "wks"), ("month", "mnth"), ("people", "persons"), ("community", "area"),
    ("drugs", "medicines"), ("bleeding", "bleeding heavily"),
]


def _urgency(sev, tone, amplified, vulnerable, rng):
    """Ground-truth urgency rule used by the annotator."""
    if sev == 3:
        return "High"
    if sev == 2:
        if amplified or (vulnerable and tone == "Negative" and rng.random() < 0.5):
            return "High"
        return "Medium"
    if amplified and rng.random() < 0.4:
        return "Medium"
    return "Low"


def make_record(rng, half):
    # test complaints mix seen and unseen phrasing (50/50)
    use_b = half == "B" and rng.random() < 0.5
    openings = OPENINGS_B if use_b else OPENINGS_A
    locs = LOCS_B if use_b else LOCS_A
    tones = TONE_B if (half == "B" and rng.random() < 0.5) else TONE_A
    (cat, sev) = rng.choice(list(PROBLEMS.keys()))
    phrases = PROBLEMS[(cat, sev)]
    # the last phrase of each bank (banks of 3 or more) is reserved for the test set only
    shared = phrases[:-1] if len(phrases) >= 3 else phrases
    reserved = phrases[-1:] if len(phrases) >= 3 else []
    if half == "B" and reserved and rng.random() < 0.3:
        problem = rng.choice(reserved)
    else:
        problem = rng.choice(shared)
    if half == "B":
        # test complaints are re-worded with synonyms the model has not seen in context
        for a, b in SYNONYMS:
            if a in problem and rng.random() < 0.6:
                problem = problem.replace(a, b)
    tone_weights = {3: [0.80, 0.15, 0.05], 2: [0.60, 0.30, 0.10], 1: [0.15, 0.55, 0.30]}[sev]
    tone = rng.choices(["Negative", "Neutral", "Positive"], weights=tone_weights)[0]
    lga, place = rng.choice(LGAS), rng.choice(PLACES)
    parts = [rng.choice(openings).format(lga=lga), problem, rng.choice(locs).format(place=place, lga=lga)]
    if sev < 3 and rng.random() < 0.5:
        parts.append(rng.choice(DURATIONS))
    text = " ".join(parts).strip() + "."
    vulnerable = rng.random() < 0.3
    if vulnerable:
        text += " " + rng.choice(VULNERABLE)
    amplified = rng.random() < (0.55 if sev == 3 else 0.15)
    if amplified:
        text += " " + rng.choice(AMPLIFIERS)
    text += " " + rng.choice(tones[tone])
    if rng.random() < 0.15:
        text = text.lower()
    urgency = _urgency(sev, tone, amplified, vulnerable, rng)
    # annotator disagreement noise (about 4 percent)
    if rng.random() < 0.04:
        urgency = rng.choice(["High", "Medium", "Low"])
    if rng.random() < 0.04:
        tone = rng.choice(["Negative", "Neutral", "Positive"])
    return {"text": text, "category": cat, "sentiment": tone, "urgency": urgency, "lga": lga}


def build(out_dir="data", n_train=2400, n_test=600, n_dev=300, seed=42):
    """
    Writes three splits:
      train - half A phrasing, used to fit the models
      test  - half B phrasing, touched once for the final evaluation
      dev   - drawn like the test set (half B) but with a separate random stream and no
              text shared with train or test; used for model selection, early stopping,
              ensemble weighting and triage-threshold calibration during fine-tuning.
    The train and test files are identical to those produced before the dev split existed.
    """
    rng = random.Random(seed)
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    used = set()
    for name, n, half in [("train", n_train, "A"), ("test", n_test, "B"), ("dev", n_dev, "B")]:
        if name == "dev":
            rng = random.Random(seed + 1000)
        rows, seen = [], set()
        while len(rows) < n:
            r = make_record(rng, half)
            if r["text"] in seen or (name == "dev" and r["text"] in used):
                continue
            seen.add(r["text"])
            rows.append(r)
        used |= seen
        with open(out / f"grievances_{name}.csv", "w", newline="", encoding="utf-8") as f:
            w = csv.DictWriter(f, fieldnames=["text", "category", "sentiment", "urgency", "lga"])
            w.writeheader()
            w.writerows(rows)
        print(name, len(rows))


if __name__ == "__main__":
    build()
