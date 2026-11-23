"""
NLP layer of the CitizenVoice agent: text cleaning, the sentiment / category / urgency
models and the hybrid urgency-triage scorer.

Model serving
-------------
`Models` loads whatever the training scripts produced:

* models/models.pkl          baseline TF-IDF pipelines (python -m app.train)
* models/models_tuned.pkl    tuned TF-IDF pipelines     (python -m app.finetune)
* models/transformer/        fine-tuned multi-task MiniLM (python -m app.finetune)
* models/selection.json      per-task blend weights and calibrated triage thresholds

For each task the served probability is  w * transformer + (1 - w) * classical,
with w chosen on the dev set. If PyTorch or the transformer files are missing, the
service falls back to the classical models automatically.
"""
import json
import logging
import pickle
import re
from collections import OrderedDict
from pathlib import Path

from vaderSentiment.vaderSentiment import SentimentIntensityAnalyzer

from . import config

log = logging.getLogger("citizenvoice.nlp")
MODEL_DIR = config.MODEL_DIR
TASKS = ("sentiment", "urgency", "category")
DEFAULT_THRESHOLDS = {"high": 45.0, "medium": 22.0}

# Nigerian English / Pidgin normalisation applied before vectorisation
NORMALISE = {
    r"\babeg\b": "please", r"\bbiko\b": "please", r"\bna wa\b": "this is bad",
    r"\bwahala\b": "trouble", r"\bno light\b": "no electricity", r"\bnepa\b": "electricity",
    r"\bdey\b": "is", r"\bo\b": "", r"\bpikin\b": "child", r"\bdon\b": "has",
}

# critical life-threat lexicon (perceived as hazards in the environment)
CRITICAL_TERMS = [
    "shooting", "gun", "gunmen", "kidnap", "abduct", "bandit", "stab", "bleeding", "injured",
    "fire", "burning", "explod", "trapped", "collapsed", "collaps", "swept", "drown",
    "stranded", "died", "dead", "dying", "convuls", "cholera", "outbreak", "electrocut",
    "sparking", "live wire", "wire is live", "accident", "toxic", "chemical", "labour",
    "snake bite", "attack", "robbing", "clash", "riot", "caved in", "crashed",
]
VULNERABLE_TERMS = ["child", "children", "pupil", "pregnant", "labour", "old people",
                    "elderly", "school", "students", "families", "disabled"]
IMMEDIACY_TERMS = ["now", "right now", "as i type", "tonight", "immediately", "hurry",
                   "emergency", "fast", "urgent", "!!!", "lives are at risk", "come now"]

_vader = SentimentIntensityAnalyzer()


def clean(text: str) -> str:
    t = text.lower()
    for pat, rep in NORMALISE.items():
        t = re.sub(pat, rep, t)
    t = re.sub(r"[^a-z0-9!\s']", " ", t)
    return re.sub(r"\s+", " ", t).strip()


def lexicon_hits(text: str, terms) -> list:
    t = text.lower()
    return sorted({term for term in terms if term in t})


class Models:
    """Loads the trained models once and serves blended class probabilities."""

    def __init__(self, model_dir: Path = None, baseline: bool = False, use_transformer: bool = None):
        model_dir = Path(model_dir or MODEL_DIR)
        sel_path = model_dir / "selection.json"
        sel = {} if baseline or not sel_path.exists() else json.loads(sel_path.read_text())
        pkl = model_dir / sel.get("classical", "models.pkl")
        with open(pkl, "rb") as f:
            bundle = pickle.load(f)
        self.classical = {t: bundle[t] for t in TASKS}
        self.weights = {t: float(sel.get("weights", {}).get(t, 0.0)) for t in TASKS}
        self.thresholds = {**DEFAULT_THRESHOLDS, **sel.get("thresholds", {})}
        self.transformer = None
        if use_transformer is None:
            use_transformer = config.USE_TRANSFORMER
        if use_transformer and any(self.weights.values()) and (model_dir / "transformer").exists():
            try:
                from .transformer_model import TransformerPredictor
                self.transformer = TransformerPredictor(model_dir / "transformer")
            except Exception as exc:  # torch missing or files damaged: degrade gracefully
                log.warning("Transformer model unavailable (%s); using classical models only", exc)
        if self.transformer is None:
            self.weights = {t: 0.0 for t in TASKS}
        # kept for backwards compatibility with older callers
        self.sentiment, self.urgency, self.category = (self.classical[t] for t in TASKS)
        algos = bundle.get("meta", {})
        self.meta = {t: self._describe(t, algos.get(t, "TF-IDF")) for t in TASKS}
        self._cache: OrderedDict = OrderedDict()

    def _describe(self, task, algo):
        w = self.weights[task]
        if w >= 1:
            return "Fine-tuned MiniLM transformer"
        if w > 0:
            return f"Ensemble: {w:.0%} fine-tuned MiniLM + {1 - w:.0%} {algo}"
        return algo

    @property
    def backend(self) -> str:
        return "transformer+classical" if self.transformer else "classical"

    def predict(self, cleaned: str) -> dict:
        """Returns {task: {label: probability}} for one cleaned text (memoised)."""
        if cleaned in self._cache:
            self._cache.move_to_end(cleaned)
            return self._cache[cleaned]
        out = {}
        tf = self.transformer.predict_proba([cleaned]) if self.transformer else None
        for t in TASKS:
            pipe = self.classical[t]
            probs = dict(zip(pipe.classes_, (float(p) for p in pipe.predict_proba([cleaned])[0])))
            w = self.weights[t]
            if tf is not None and w > 0:
                probs = {k: w * tf[t][0].get(k, 0.0) + (1 - w) * v for k, v in probs.items()}
            out[t] = probs
        self._cache[cleaned] = out
        if len(self._cache) > 512:
            self._cache.popitem(last=False)
        return out


def triage(text: str, models: "Models", cluster_count: int = 0) -> dict:
    """
    Hybrid urgency triage. Combines:
      p_high, p_med   - probabilities from the urgency classifier
      neg             - negative sentiment intensity (model probability blended with VADER)
      critical        - life-threat lexicon evidence
      vulnerable      - presence of vulnerable groups
      immediacy       - present-tense / immediacy cues
      cluster         - number of similar reports from the same LGA in the last 24 hours
    Returns a score on a 0-100 scale, the priority level and an explanation trace.
    Level thresholds are calibrated on the dev set during fine-tuning.
    """
    p = models.predict(clean(text))
    s_proba, u_proba, k_proba = p["sentiment"], p["urgency"], p["category"]
    sentiment = max(s_proba, key=s_proba.get)
    category = max(k_proba, key=k_proba.get)
    vader = _vader.polarity_scores(text)["compound"]
    neg = 0.6 * s_proba.get("Negative", 0) + 0.4 * max(0.0, -vader)
    crit = lexicon_hits(text, CRITICAL_TERMS)
    vul = lexicon_hits(text, VULNERABLE_TERMS)
    imm = lexicon_hits(text, IMMEDIACY_TERMS)
    p_high, p_med = u_proba.get("High", 0), u_proba.get("Medium", 0)
    score = (55 * p_high + 25 * p_med + 12 * neg
             + 15 * min(len(crit), 2) / 2 + 4 * (1 if vul else 0)
             + 4 * min(len(imm), 2) / 2 + min(cluster_count, 5))
    score = round(min(score, 100.0), 1)
    th = models.thresholds
    if score >= th["high"] or (len(crit) >= 2 and imm):
        level = "High"
    elif score >= th["medium"]:
        level = "Medium"
    else:
        level = "Low"
    return {
        "sentiment": sentiment,
        "sentiment_confidence": round(float(s_proba[sentiment]), 3),
        "vader_compound": round(vader, 3),
        "category": category,
        "category_confidence": round(float(k_proba[category]), 3),
        "urgency_model": max(u_proba, key=u_proba.get),
        "p_high": round(float(p_high), 3),
        "p_medium": round(float(p_med), 3),
        "critical_terms": crit,
        "vulnerable_terms": vul,
        "immediacy_terms": imm,
        "cluster_count": cluster_count,
        "score": score,
        "urgency": level,
        "model_backend": models.backend,
        "thresholds": {"high": th["high"], "medium": th["medium"]},
    }
