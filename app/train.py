"""
Trains and evaluates the CitizenVoice NLP models.

For each task (sentiment, urgency, category) three classical algorithms are compared:
Multinomial Naive Bayes, Logistic Regression and Linear SVM (calibrated), all on the
same TF-IDF features (word unigrams + bigrams). The best model on 5-fold cross-validation
of the training set is kept, then evaluated once on the held-out test set.
The hybrid triage (app.nlp.triage) is also evaluated on the test set against two
baselines: first-come-first-served (everything Medium) and a keyword-only rule.
"""
import json
import pickle
import time
import sys
from pathlib import Path

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from sklearn.pipeline import Pipeline
from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.naive_bayes import MultinomialNB
from sklearn.linear_model import LogisticRegression
from sklearn.svm import LinearSVC
from sklearn.calibration import CalibratedClassifierCV
from sklearn.model_selection import cross_val_score, StratifiedKFold
from sklearn.metrics import (accuracy_score, precision_recall_fscore_support,
                             confusion_matrix, classification_report)

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app.nlp import clean, triage, Models, lexicon_hits, CRITICAL_TERMS  # noqa: E402

plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "DejaVu Serif"],
                     "font.size": 11})


def make(algo):
    vec = TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)
    clf = {
        "Naive Bayes": MultinomialNB(alpha=0.3),
        "Logistic Regression": LogisticRegression(max_iter=2000, C=4.0),
        "Linear SVM": CalibratedClassifierCV(LinearSVC(C=0.8), cv=3),
    }[algo]
    return Pipeline([("tfidf", vec), ("clf", clf)])


def plot_cm(cm, labels, title, path):
    fig, ax = plt.subplots(figsize=(4.6, 3.9), dpi=200)
    ax.imshow(cm, cmap="Greys")
    ax.set_xticks(range(len(labels)), labels)
    ax.set_yticks(range(len(labels)), labels)
    ax.set_xlabel("Predicted label")
    ax.set_ylabel("True label")
    thr = cm.max() / 2
    for i in range(len(labels)):
        for j in range(len(labels)):
            ax.text(j, i, cm[i, j], ha="center", va="center",
                    color="white" if cm[i, j] > thr else "black", fontsize=11)
    ax.set_title(title, fontsize=11)
    fig.tight_layout()
    fig.savefig(path)
    plt.close(fig)


def main():
    tr = pd.read_csv(ROOT / "data/grievances_train.csv")
    te = pd.read_csv(ROOT / "data/grievances_test.csv")
    Xtr, Xte = tr.text.map(clean), te.text.map(clean)
    rep = {"n_train": len(tr), "n_test": len(te),
           "label_dist": {t: tr[t].value_counts().to_dict() for t in ["sentiment", "urgency", "category"]},
           "label_dist_test": {t: te[t].value_counts().to_dict() for t in ["sentiment", "urgency", "category"]},
           "tasks": {}}
    best = {}
    skf = StratifiedKFold(n_splits=5, shuffle=True, random_state=1)
    for task in ["sentiment", "urgency", "category"]:
        rep["tasks"][task] = {}
        scores = {}
        for algo in ["Naive Bayes", "Logistic Regression", "Linear SVM"]:
            cv = cross_val_score(make(algo), Xtr, tr[task], cv=skf, scoring="f1_macro")
            m = make(algo)
            t0 = time.perf_counter(); m.fit(Xtr, tr[task]); fit_s = time.perf_counter() - t0
            pred = m.predict(Xte)
            p, r, f, _ = precision_recall_fscore_support(te[task], pred, average="macro", zero_division=0)
            rep["tasks"][task][algo] = {"cv_f1": round(cv.mean(), 4), "cv_std": round(cv.std(), 4),
                                        "accuracy": round(accuracy_score(te[task], pred), 4),
                                        "precision": round(p, 4), "recall": round(r, 4), "f1": round(f, 4),
                                        "fit_seconds": round(fit_s, 3)}
            scores[algo] = (cv.mean(), m)
            print(task, algo, rep["tasks"][task][algo])
        algo = max(scores, key=lambda a: scores[a][0])
        best[task] = scores[algo][1]
        pred = best[task].predict(Xte)
        labels = {"sentiment": ["Negative", "Neutral", "Positive"],
                  "urgency": ["High", "Medium", "Low"]}.get(task, sorted(tr[task].unique()))
        rep["tasks"][task]["selected"] = algo
        rep["tasks"][task]["report"] = classification_report(te[task], pred, output_dict=True, zero_division=0)
        cm = confusion_matrix(te[task], pred, labels=labels)
        rep["tasks"][task]["confusion"] = {"labels": labels, "matrix": cm.tolist()}
        if task != "category":
            plot_cm(cm, labels, f"{task.title()} ({algo})", ROOT / f"reports/cm_{task}.png")

    with open(ROOT / "models/models.pkl", "wb") as f:
        pickle.dump({"sentiment": best["sentiment"], "urgency": best["urgency"],
                     "category": best["category"],
                     "meta": {k: rep["tasks"][k]["selected"] for k in best}}, f)

    # ---- hybrid triage evaluation ----
    models = Models(baseline=True)
    labels = ["High", "Medium", "Low"]
    t0 = time.perf_counter()
    hyb = [triage(t, models)["urgency"] for t in te.text]
    per_msg_ms = (time.perf_counter() - t0) / len(te) * 1000
    kw = ["High" if lexicon_hits(t, CRITICAL_TERMS) else "Low" if len(t) < 0 else "Medium" for t in te.text]
    fcfs = ["Medium"] * len(te)
    rep["triage"] = {}
    for name, pred in [("FCFS (no triage)", fcfs), ("Keyword rule only", kw),
                       ("ML urgency model only", list(best["urgency"].predict(Xte))),
                       ("Hybrid agent triage", hyb)]:
        p, r, f, _ = precision_recall_fscore_support(te.urgency, pred, average="macro", zero_division=0)
        pr_h, rc_h, f_h, _ = precision_recall_fscore_support(te.urgency, pred, labels=["High"], zero_division=0)
        rep["triage"][name] = {"accuracy": round(accuracy_score(te.urgency, pred), 4), "macro_f1": round(f, 4),
                               "high_precision": round(float(pr_h[0]), 4), "high_recall": round(float(rc_h[0]), 4)}
        print(name, rep["triage"][name])
    cm = confusion_matrix(te.urgency, hyb, labels=labels)
    rep["triage_confusion"] = cm.tolist()
    rep["triage_ms_per_message"] = round(per_msg_ms, 2)
    plot_cm(cm, labels, "Hybrid Urgency Triage", ROOT / "reports/cm_triage.png")

    # model comparison chart (greyscale)
    fig, axes = plt.subplots(1, 3, figsize=(9.5, 3.4), dpi=200, sharey=True)
    hatches = ["", "///", "..."]
    shades = ["#222222", "#888888", "#dddddd"]
    for ax, task in zip(axes, ["sentiment", "urgency", "category"]):
        algos = ["Naive Bayes", "Logistic Regression", "Linear SVM"]
        vals = [rep["tasks"][task][a]["f1"] * 100 for a in algos]
        bars = ax.bar(range(3), vals, color=shades, edgecolor="black", hatch=None)
        for b, h in zip(bars, hatches):
            b.set_hatch(h)
        for i, v in enumerate(vals):
            ax.text(i, v + 1, f"{v:.1f}", ha="center", fontsize=9)
        ax.set_xticks(range(3), ["NB", "LR", "SVM"])
        ax.set_title(task.title(), fontsize=11)
        ax.set_ylim(0, 105)
    axes[0].set_ylabel("Macro F1-score (%)")
    fig.tight_layout()
    fig.savefig(ROOT / "reports/model_comparison.png")
    plt.close(fig)

    # triage comparison chart
    fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=200)
    names = list(rep["triage"].keys())
    x = np.arange(len(names))
    acc = [rep["triage"][n]["macro_f1"] * 100 for n in names]
    rec = [rep["triage"][n]["high_recall"] * 100 for n in names]
    b1 = ax.bar(x - 0.18, acc, 0.36, color="#444444", edgecolor="black", label="Macro F1 (%)")
    b2 = ax.bar(x + 0.18, rec, 0.36, color="white", edgecolor="black", hatch="///", label="High-urgency recall (%)")
    for bars in (b1, b2):
        for b in bars:
            ax.text(b.get_x() + b.get_width() / 2, b.get_height() + 1, f"{b.get_height():.1f}", ha="center", fontsize=8)
    ax.set_xticks(x, ["FCFS\n(no triage)", "Keyword\nrule only", "ML urgency\nmodel only", "Hybrid agent\ntriage"])
    ax.set_ylim(0, 110)
    ax.legend(fontsize=9, loc="upper left")
    fig.tight_layout()
    fig.savefig(ROOT / "reports/triage_comparison.png")
    plt.close(fig)

    with open(ROOT / "reports/metrics.json", "w") as f:
        json.dump(rep, f, indent=2, default=float)
    print("triage ms/message", per_msg_ms)


if __name__ == "__main__":
    main()
