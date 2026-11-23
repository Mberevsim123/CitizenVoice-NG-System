"""
Time-budgeted fine-tuning of the CitizenVoice models (default budget: 30 minutes).

    python -m app.finetune                # 30-minute run
    python -m app.finetune --minutes 5    # quick run

Stages (all model selection uses the dev split; the test split is used once, at the end)

1. Classical tuning      TF-IDF word / word+character features x Logistic Regression /
                         calibrated Linear SVM over a small C grid, per task.
2. Transformer           multi-task fine-tuning of MiniLM-L6 (shared encoder, three heads)
                         for the rest of the budget. The learning rate follows a time-based
                         warm-up + cosine schedule, so the run always finishes on budget.
                         The checkpoint with the best dev macro-F1 is kept.
3. Blending              per task, the weight w of  w*transformer + (1-w)*classical  that
                         maximises dev macro-F1.
4. Triage calibration    High / Medium score thresholds of the hybrid triage are re-fitted
                         on the dev set, with the rule that High-urgency recall must not
                         fall below that of the plain urgency model.
5. Final evaluation      all variants on the held-out test set -> reports/finetune.json and
                         charts; served models written to models/.
"""
import argparse
import copy
import json
import math
import pickle
import random
import sys
import time
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from app import config  # noqa: E402
from app.nlp import clean, triage, Models, TASKS, CRITICAL_TERMS, lexicon_hits  # noqa: E402

MODEL_DIR, REPORT_DIR, DATA = config.MODEL_DIR, config.REPORT_DIR, ROOT / "data"
LABELS = {"sentiment": ["Negative", "Neutral", "Positive"], "urgency": ["High", "Medium", "Low"]}
TASK_WEIGHT = {"sentiment": 1.0, "urgency": 1.5, "category": 1.0}   # urgency drives triage


def log(msg):
    print(f"[{time.strftime('%H:%M:%S')}] {msg}", flush=True)


def macro_f1(y, pred):
    from sklearn.metrics import f1_score
    return float(f1_score(y, pred, average="macro", zero_division=0))


def load_splits():
    paths = {s: DATA / f"grievances_{s}.csv" for s in ("train", "dev", "test")}
    if not all(p.exists() for p in paths.values()):
        from app.dataset import build
        build(out_dir=DATA)
    return {s: pd.read_csv(p) for s, p in paths.items()}


# ---------------------------------------------------------------- stage 1: classical
def tune_classical(tr, dv, deadline):
    from sklearn.pipeline import Pipeline, FeatureUnion
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.linear_model import LogisticRegression
    from sklearn.svm import LinearSVC
    from sklearn.calibration import CalibratedClassifierCV

    feats = {
        "word 1-2": lambda: TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True),
        "word 1-2 + char 2-5": lambda: FeatureUnion([
            ("w", TfidfVectorizer(ngram_range=(1, 2), min_df=2, sublinear_tf=True)),
            ("c", TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), min_df=2, sublinear_tf=True))]),
    }
    clfs = {f"LR C={c}": (lambda c=c: LogisticRegression(max_iter=3000, C=c)) for c in (1.0, 4.0, 12.0)}
    clfs.update({f"SVM C={c}": (lambda c=c: CalibratedClassifierCV(LinearSVC(C=c), cv=3)) for c in (0.3, 1.0)})
    Xtr, Xdv = tr.text.map(clean).tolist(), dv.text.map(clean).tolist()
    best, trials = {}, []
    for fname, fmake in feats.items():
        vec = fmake().fit(Xtr)
        Mtr, Mdv = vec.transform(Xtr), vec.transform(Xdv)
        for cname, cmake in clfs.items():
            for task in TASKS:
                if time.time() > deadline and task in best:
                    continue
                clf = cmake().fit(Mtr, tr[task])
                f1 = macro_f1(dv[task], clf.predict(Mdv))
                trials.append({"task": task, "features": fname, "classifier": cname, "dev_f1": round(f1, 4)})
                if task not in best or f1 > best[task][0]:
                    best[task] = (f1, Pipeline([("tfidf", vec), ("clf", clf)]), f"{cname}, {fname}")
            log(f"classical {fname:20s} {cname:9s} " +
                " ".join(f"{t}={best[t][0]:.3f}" for t in TASKS if t in best))
    return best, trials


# ---------------------------------------------------------------- stage 2: transformer
def finetune_transformer(tr, dv, labels, seconds, seed=13, batch_size=16, max_len=96):
    import torch
    from transformers import AutoTokenizer
    from app.transformer_model import MultiTaskClassifier, BASE_MODEL, load_encoder

    torch.manual_seed(seed)
    random.seed(seed)
    torch.set_num_threads(max(1, torch.get_num_threads()))
    log(f"loading {BASE_MODEL}")
    tok = AutoTokenizer.from_pretrained(BASE_MODEL)
    model = MultiTaskClassifier(load_encoder(BASE_MODEL), {t: len(v) for t, v in labels.items()})

    def encode(df):
        enc = tok(df.text.map(clean).tolist(), truncation=True, max_length=max_len)
        y = {t: torch.tensor([labels[t].index(v) for v in df[t]]) for t in TASKS}
        return enc["input_ids"], y

    ids_tr, y_tr = encode(tr)
    ids_dv, y_dv = encode(dv)

    def batch(ids, idx):
        b = tok.pad({"input_ids": [ids[i] for i in idx]}, return_tensors="pt")
        return b["input_ids"], b["attention_mask"]

    @torch.no_grad()
    def evaluate(ids, y):
        model.eval()
        probs = {t: [] for t in TASKS}
        for i in range(0, len(ids), 32):
            x, m = batch(ids, range(i, min(i + 32, len(ids))))
            out = model(x, m)
            for t in TASKS:
                probs[t].append(torch.softmax(out[t], -1))
        model.train()
        probs = {t: torch.cat(v).numpy() for t, v in probs.items()}
        f1 = {t: macro_f1(y[t].numpy(), probs[t].argmax(1)) for t in TASKS}
        return f1, probs

    enc_params = list(model.encoder.parameters())
    opt = torch.optim.AdamW([{"params": enc_params, "lr": 5e-5, "base": 5e-5},
                             {"params": model.heads.parameters(), "lr": 1e-3, "base": 1e-3}],
                            weight_decay=0.01)
    loss_fn = torch.nn.CrossEntropyLoss(label_smoothing=0.05)

    def lr_factor(frac):            # 6% linear warm-up, then cosine decay to 0 at the deadline
        return frac / 0.06 if frac < 0.06 else 0.5 * (1 + math.cos(math.pi * (frac - 0.06) / 0.94))

    t0 = time.time()
    end = t0 + seconds
    history, best = [], {"score": -1}
    step, epoch, n = 0, 0, len(ids_tr)
    model.train()
    while time.time() < end:
        epoch += 1
        order = list(range(n))
        random.shuffle(order)
        run_loss, seen = 0.0, 0
        for i in range(0, n, batch_size):
            frac = (time.time() - t0) / seconds
            if frac >= 1:
                break
            for g in opt.param_groups:
                g["lr"] = g["base"] * lr_factor(frac)
            idx = order[i:i + batch_size]
            x, m = batch(ids_tr, idx)
            out = model(x, m)
            loss = sum(TASK_WEIGHT[t] * loss_fn(out[t], y_tr[t][idx]) for t in TASKS)
            opt.zero_grad()
            loss.backward()
            torch.nn.utils.clip_grad_norm_(model.parameters(), 1.0)
            opt.step()
            step += 1
            run_loss += loss.item() * len(idx)
            seen += len(idx)
        f1, _ = evaluate(ids_dv, y_dv)
        score = float(np.mean(list(f1.values())))
        rec = {"epoch": epoch, "steps": step, "minutes": round((time.time() - t0) / 60, 2),
               "train_loss": round(run_loss / max(seen, 1), 4), "dev_f1": {t: round(v, 4) for t, v in f1.items()},
               "dev_mean_f1": round(score, 4), "partial_epoch": seen < n}
        history.append(rec)
        log(f"epoch {epoch:2d} ({rec['minutes']:5.1f} min, {seen}/{n} ex) loss {rec['train_loss']:.3f} "
            f"dev F1 " + " ".join(f"{t}={v:.3f}" for t, v in f1.items()))
        if score > best["score"]:
            best = {"score": score, "epoch": epoch, "state": copy.deepcopy(model.state_dict())}
    model.load_state_dict(best["state"])
    model.eval()
    _, dev_probs = evaluate(ids_dv, y_dv)
    info = {"base_model": BASE_MODEL, "epochs": epoch, "steps": step, "best_epoch": best["epoch"],
            "train_minutes": round((time.time() - t0) / 60, 2), "batch_size": batch_size, "max_len": max_len,
            "history": history}
    return model, tok, dev_probs, info


# ---------------------------------------------------------------- helpers
def classical_probs(pipe, texts, labels):
    p = pipe.predict_proba([clean(t) for t in texts])
    cols = [list(pipe.classes_).index(l) for l in labels]
    return p[:, cols]


def choose_weight(y, p_cls, p_tf, labels, max_weight=1.0):
    best = (-1, 0.0)
    for w in np.linspace(0, 1, 11):
        if w > max_weight + 1e-9:
            continue
        f1 = macro_f1(y, [labels[i] for i in (w * p_tf + (1 - w) * p_cls).argmax(1)])
        # ties go to the weight closest to an even blend (more robust than either model alone)
        if f1 > best[0] + 1e-9 or (abs(f1 - best[0]) < 1e-9 and abs(w - 0.5) < abs(best[1] - 0.5)):
            best = (f1, float(w))
    return best[1], best[0]


def calibrate_thresholds(dv, models):
    from sklearn.metrics import recall_score
    rows = [triage(t, models) for t in dv.text]
    scores = np.array([r["score"] for r in rows])
    override = np.array([len(r["critical_terms"]) >= 2 and bool(r["immediacy_terms"]) for r in rows])
    floor = recall_score(dv.urgency, [r["urgency_model"] for r in rows], labels=["High"], average="macro",
                         zero_division=0)
    best = None
    for hi in np.arange(30, 70.5, 0.5):
        for me in np.arange(8, hi - 4, 0.5):
            pred = np.where((scores >= hi) | override, "High", np.where(scores >= me, "Medium", "Low"))
            rec = recall_score(dv.urgency, pred, labels=["High"], average="macro", zero_division=0)
            if rec + 1e-9 < floor:
                continue
            f1 = macro_f1(dv.urgency, pred)
            if best is None or f1 > best[0] + 1e-9:
                best = (f1, float(hi), float(me), float(rec))
    return {"high": best[1], "medium": best[2]}, {"dev_macro_f1": round(best[0], 4),
                                                   "dev_high_recall": round(best[3], 4),
                                                   "high_recall_floor": round(float(floor), 4)}


def test_metrics(y, pred, positive="High"):
    from sklearn.metrics import accuracy_score, precision_recall_fscore_support
    p, r, f, _ = precision_recall_fscore_support(y, pred, average="macro", zero_division=0)
    out = {"accuracy": round(accuracy_score(y, pred), 4), "precision": round(p, 4), "recall": round(r, 4),
           "f1": round(f, 4)}
    if positive in set(y):
        ph, rh, _, _ = precision_recall_fscore_support(y, pred, labels=[positive], zero_division=0)
        out.update(high_precision=round(float(ph[0]), 4), high_recall=round(float(rh[0]), 4))
    return out


def charts(rep):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    plt.rcParams.update({"font.family": "serif", "font.serif": ["Times New Roman", "DejaVu Serif"],
                         "font.size": 10})
    hist = rep["transformer"]["history"]
    fig, ax = plt.subplots(figsize=(7.2, 3.4), dpi=200)
    x = [h["minutes"] for h in hist]
    for t, style in zip(TASKS, ["-o", "--s", ":^"]):
        ax.plot(x, [h["dev_f1"][t] * 100 for h in hist], style, color="black", ms=3.5, label=t.title())
    ax.set_xlabel("Training time (minutes)")
    ax.set_ylabel("Dev macro F1 (%)")
    ax2 = ax.twinx()
    ax2.plot(x, [h["train_loss"] for h in hist], color="#999999", lw=1, label="Train loss")
    ax2.set_ylabel("Training loss", color="#777777")
    ax.legend(loc="lower right", fontsize=8.5)
    ax.set_title("MiniLM multi-task fine-tuning", fontsize=10.5)
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "finetune_curve.png")
    plt.close(fig)

    variants = ["Baseline TF-IDF", "Tuned TF-IDF", "Fine-tuned MiniLM", "Served (blend)"]
    shades = ["#dddddd", "#aaaaaa", "#666666", "#222222"]
    fig, axes = plt.subplots(1, 3, figsize=(9.5, 3.4), dpi=200, sharey=True)
    for ax, t in zip(axes, TASKS):
        vals = [rep["test"][t][v]["f1"] * 100 for v in variants]
        ax.bar(range(4), vals, color=shades, edgecolor="black")
        for i, v in enumerate(vals):
            ax.text(i, v + 1, f"{v:.1f}", ha="center", fontsize=8)
        ax.set_xticks(range(4), ["Base", "Tuned", "MiniLM", "Blend"], fontsize=8.5)
        ax.set_title(t.title(), fontsize=10.5)
        ax.set_ylim(0, 105)
    axes[0].set_ylabel("Test macro F1 (%)")
    fig.tight_layout()
    fig.savefig(REPORT_DIR / "finetune_comparison.png")
    plt.close(fig)


# ---------------------------------------------------------------- main
def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    ap.add_argument("--minutes", type=float, default=30.0, help="total wall-clock budget (default 30)")
    ap.add_argument("--classical-share", type=float, default=0.12,
                    help="fraction of the budget for classical tuning (default 0.12)")
    ap.add_argument("--max-weight", type=float, default=0.8,
                    help="largest transformer share in the blend (default 0.8). Keeping the classical model "
                         "in every blend guards against spurious cues the transformer can pick up from a "
                         "small corpus (for example polite thanks raising P(High urgency)).")
    ap.add_argument("--reblend", action="store_true",
                    help="skip training: reuse the saved tuned-classical and transformer models and redo "
                         "blending, triage calibration and evaluation")
    args = ap.parse_args(argv)
    start = time.time()
    budget = args.minutes * 60
    reserve = min(150.0, 0.1 * budget)              # time kept back for calibration + final evaluation
    REPORT_DIR.mkdir(exist_ok=True)
    MODEL_DIR.mkdir(exist_ok=True)

    sp = load_splits()
    tr, dv, te = sp["train"], sp["dev"], sp["test"]
    labels = {**LABELS, "category": sorted(tr.category.unique())}
    log(f"budget {args.minutes:.0f} min | train {len(tr)}  dev {len(dv)}  test {len(te)}")

    if args.reblend:
        prev = json.loads((REPORT_DIR / "finetune.json").read_text())
        with open(MODEL_DIR / "models_tuned.pkl", "rb") as f:
            tuned = {t: v for t, v in pickle.load(f).items() if t in TASKS}
        best_cls = {t: (prev["classical"][t]["dev_f1"], tuned[t], prev["classical"][t]["selected"]) for t in TASKS}
        trials, tf_info = prev["classical_trials"], prev["transformer"]
        args.minutes = prev["budget_minutes"]
        from app.transformer_model import TransformerPredictor
        pr = TransformerPredictor(MODEL_DIR / "transformer").predict_proba([clean(x) for x in dv.text])
        dev_tf = {t: np.array([[r[l] for l in labels[t]] for r in pr[t]]) for t in TASKS}
        log("reblend: reusing saved models")
    else:
        # 1. classical
        best_cls, trials = tune_classical(tr, dv, start + args.classical_share * budget)
        tuned = {t: best_cls[t][1] for t in TASKS}
        with open(MODEL_DIR / "models_tuned.pkl", "wb") as f:
            pickle.dump({**tuned, "meta": {t: f"Tuned TF-IDF ({best_cls[t][2]})" for t in TASKS}}, f)
        log("classical done: " + ", ".join(f"{t}: {best_cls[t][2]} dev F1 {best_cls[t][0]:.3f}" for t in TASKS))

        # 2. transformer
        tf_seconds = budget - (time.time() - start) - reserve
        log(f"transformer fine-tuning for {tf_seconds / 60:.1f} min")
        model, tok, dev_tf, tf_info = finetune_transformer(tr, dv, labels, tf_seconds)
        from app.transformer_model import save
        save(model, tok, labels, tf_info["max_len"], MODEL_DIR / "transformer")

    # 3. blending weights
    weights, blend_dev = {}, {}
    for t in TASKS:
        p_cls = classical_probs(tuned[t], dv.text, labels[t])
        weights[t], blend_dev[t] = choose_weight(dv[t], p_cls, dev_tf[t], labels[t], args.max_weight)
        log(f"blend {t}: w_transformer={weights[t]:.1f} dev F1 {blend_dev[t]:.3f}")
    sel = {"classical": "models_tuned.pkl", "weights": weights, "thresholds": {"high": 45.0, "medium": 22.0},
           "created": time.strftime("%Y-%m-%d %H:%M:%S")}
    (MODEL_DIR / "selection.json").write_text(json.dumps(sel, indent=1))

    # 4. triage thresholds
    served = Models(use_transformer=True)
    sel["thresholds"], calib = calibrate_thresholds(dv, served)
    (MODEL_DIR / "selection.json").write_text(json.dumps(sel, indent=1))
    served.thresholds = sel["thresholds"]
    served._cache.clear()
    log(f"triage thresholds {sel['thresholds']} {calib}")

    # 5. test evaluation (first and only use of the test split)
    base = Models(baseline=True)
    from app.transformer_model import TransformerPredictor
    tf_pred = TransformerPredictor(MODEL_DIR / "transformer").predict_proba([clean(x) for x in te.text])
    rep = {"budget_minutes": args.minutes, "n_train": len(tr), "n_dev": len(dv), "n_test": len(te),
           "classical": {t: {"selected": best_cls[t][2], "dev_f1": round(best_cls[t][0], 4)} for t in TASKS},
           "classical_trials": trials, "transformer": tf_info, "weights": weights,
           "blend_dev_f1": {t: round(v, 4) for t, v in blend_dev.items()},
           "thresholds": sel["thresholds"], "threshold_calibration": calib, "test": {}}
    for t in TASKS:
        lab = labels[t]
        p_tf = np.array([[r[l] for l in lab] for r in tf_pred[t]])
        p_cls = classical_probs(tuned[t], te.text, lab)
        preds = {
            "Baseline TF-IDF": base.classical[t].predict(te.text.map(clean)),
            "Tuned TF-IDF": [lab[i] for i in p_cls.argmax(1)],
            "Fine-tuned MiniLM": [lab[i] for i in p_tf.argmax(1)],
            "Served (blend)": [lab[i] for i in (weights[t] * p_tf + (1 - weights[t]) * p_cls).argmax(1)],
        }
        rep["test"][t] = {k: test_metrics(te[t], v) for k, v in preds.items()}
        log(f"test {t}: " + " ".join(f"{k}={v['f1']:.3f}" for k, v in rep["test"][t].items()))

    t0 = time.perf_counter()
    hyb = [triage(x, served)["urgency"] for x in te.text]
    ms = (time.perf_counter() - t0) / len(te) * 1000
    base_hyb = [triage(x, base)["urgency"] for x in te.text]
    kw = ["High" if lexicon_hits(x, CRITICAL_TERMS) else "Medium" for x in te.text]
    rep["triage"] = {
        "FCFS (no triage)": test_metrics(te.urgency, ["Medium"] * len(te)),
        "Keyword rule only": test_metrics(te.urgency, kw),
        "Hybrid triage (baseline models)": test_metrics(te.urgency, base_hyb),
        "Hybrid triage (fine-tuned models)": test_metrics(te.urgency, hyb),
    }
    rep["triage_ms_per_message"] = round(ms, 2)
    rep["max_weight"] = args.max_weight
    if args.reblend:
        rep["total_minutes"] = prev["total_minutes"]
    else:
        rep["total_minutes"] = round((time.time() - start) / 60, 2)
    for k, v in rep["triage"].items():
        log(f"triage {k}: F1 {v['f1']:.3f} High recall {v.get('high_recall', 0):.3f}")
    (REPORT_DIR / "finetune.json").write_text(json.dumps(rep, indent=1))
    charts(rep)
    log(f"done in {rep['total_minutes']:.1f} min -> models/selection.json, models/transformer/, "
        "reports/finetune.json")


if __name__ == "__main__":
    main()
