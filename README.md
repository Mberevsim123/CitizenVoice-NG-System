# CitizenVoice NG - Government Grievance Reporting Bot with Urgency Triage

Chatbot agent with sentiment analysis and urgency triage for government grievance reporting
(Nasarawa State). Final-year project software for Gbari Akpalaakpo O. A. (FT22BCMP0517),
Department of Computer Science, Nasarawa State University, Keffi.

## Quick start

```bash
pip install -r requirements-dev.txt
python app/dataset.py          # builds data/grievances_{train,dev,test}.csv
python -m app.train            # baseline classical models + reports/metrics.json
python -m app.finetune         # 30-minute fine-tuning (see below)
python -m app.seed             # fresh database with demonstration complaints
uvicorn app.main:app --port 8000
```

Or use the Makefile: `make data train finetune seed run`, and `make test`.

Open http://localhost:8000

Local demo accounts (development only, created by `python -m app.seed`):

| Account | Login | Password |
|---|---|---|
| Grievance desk officer | admin@citizenvoice.ng | Admin@123 |
| Demo citizen | 08031000000 | Citizen@123 |

Or create a new citizen account on the home page. In production these defaults are not
available: the officer password comes from `CV_ADMIN_PASSWORD` (see Deployment).

Pages: `/` (home, sign in, register), `/chat` (chatbot), `/track` (track complaint),
`/admin` (officer console: triage queue, analytics, agency routing, model performance).

## How the agent works

`PERCEIVE -> UPDATE STATE -> DECIDE -> ACT` (see `app/agent.py`)

* **Percepts:** citizen message, conversation state, citizen profile (LGA), system clock,
  similar complaints from the same LGA in the last 24 hours (database).
* **Reasoning:** sentiment, category and urgency models plus a hybrid urgency score 0-100
  that adds danger, vulnerability and immediacy cues and report clustering (`app/nlp.py`).
* **Actions:** GREET, ASK_DETAILS, ASK_LOCATION, CONFIRM, DISPATCH_TO_AGENCY,
  ESCALATE_EMERGENCY, REPORT_STATUS, ANSWER_HELP, CANCEL.

Conversation state is saved in the database after every turn, so a half-finished report
survives a server restart.

## Models and fine-tuning

`python -m app.finetune` runs a fixed **30-minute** wall-clock budget
(`--minutes N` to change it):

1. **Classical tuning** - TF-IDF word and word+character n-grams x Logistic Regression /
   calibrated Linear SVM over a C grid, per task.
2. **Transformer fine-tuning** - `sentence-transformers/all-MiniLM-L6-v2` (22M parameters)
   with one shared encoder and three heads (sentiment, urgency, category), trained for the rest
   of the budget on a time-based warm-up + cosine learning-rate schedule. The epoch with the best
   dev macro-F1 is kept.
3. **Blending** - per task, the weight `w` in `w * transformer + (1 - w) * classical` that
   maximises dev macro-F1.
4. **Triage calibration** - the High / Medium thresholds of the hybrid urgency score are
   re-fitted on the dev set, with the constraint that High-urgency recall may not drop below
   that of the plain urgency model (missing an emergency costs more than a false alarm).
5. **Final evaluation** on the held-out test set -> `reports/finetune.json`,
   `reports/finetune_curve.png`, `reports/finetune_comparison.png`, `reports/finetune_log.txt`.

All selection uses a separate **dev split** (300 complaints drawn like the test set, sharing no
text with train or test). The test set is used once, at the end.

### Results (30-minute run: 12 epochs, 1,724 steps, best epoch 9; dual-core Intel i5 CPU)

Held-out test set, macro F1:

| Task | Baseline TF-IDF | Tuned TF-IDF | Fine-tuned MiniLM | **Served blend** |
|---|---|---|---|---|
| Sentiment | 81.1 | 82.6 | 92.7 | **93.6** (70% MiniLM) |
| Urgency | 80.1 | 81.6 | 87.9 | **88.2** (80% MiniLM) |
| Category | 84.9 | 86.9 | 90.0 | **90.2** (70% MiniLM) |

| Urgency triage | Macro F1 | High-urgency recall |
|---|---|---|
| First-come-first-served | 14.0 | 0.0 |
| Keyword rule only | 37.2 | 54.3 |
| Hybrid triage, baseline models | 76.7 | 92.5 |
| **Hybrid triage, fine-tuned models** | **84.8** | **97.6** |

The transformer's share of each blend is capped at 0.8 (`--max-weight`). Without the cap,
dev selection used the transformer alone for sentiment and urgency. It had learned a spurious
cue from the small corpus: polite thanks ("Thank you for the good work") raised its P(High
urgency) from 0.03 to 0.26. That turned a routine speed-bump request into Medium priority.
Keeping 20-30% of the classical model removes this. Test scores went slightly up for
sentiment and urgency, and the triage keeps a higher High recall (97.6 vs 97.2) for a small
macro-F1 cost (84.8 vs 86.1).

`python -m app.finetune --reblend` redoes blending, calibration and evaluation on the saved
models without retraining.

Note: the corpus is synthetic (template-built), so these figures show relative gains between
methods. They are not a measure of accuracy on real citizen messages. Collecting and
labelling real complaints is the most valuable next step.

The served models are in `models/`: `models.pkl` (baseline), `models_tuned.pkl`,
`transformer/` and `selection.json` (blend weights and thresholds). If PyTorch or the
transformer files are missing, or `CV_USE_TRANSFORMER=0` is set, the app falls back to the
classical models automatically.

## Configuration

Settings come from environment variables (see `.env.example`).

| Variable | Default | Purpose |
|---|---|---|
| `CV_ENV` | `production` on Railway, otherwise `development` | production requires `CV_ADMIN_PASSWORD`, hides `/docs`, sends HSTS |
| `CV_ADMIN_PASSWORD` | `Admin@123` in development only | first officer account password (min. 10 characters in production) |
| `CV_ADMIN_EMAIL` | `admin@citizenvoice.ng` | first officer account email |
| `CV_DB_PATH` | Railway volume, else `data/citizenvoice.db` | SQLite database file |
| `CV_MODEL_DIR` | `models` | trained models |
| `CV_TOKEN_TTL_HOURS` | `168` | sign-in session lifetime |
| `CV_USE_TRANSFORMER` | `1` | `0` serves classical models only |
| `CV_LOG_LEVEL` | `INFO` | logging level |
| `PORT` / `HOST` | `8000` / `0.0.0.0` | web server (`python -m app.serve`) |

The officer account is created once, the first time the database starts empty. Changing
`CV_ADMIN_PASSWORD` later does not change an existing account's password.

## Deployment (Railway)

The repository is ready for Railway: `railway.json` sets the start command
(`python -m app.serve`), a health check on `/api/health` and restart-on-failure. `.python-version`
pins Python 3.11.9, and `requirements.txt` installs the CPU-only PyTorch build on Linux.

1. **New project** -> **Deploy from GitHub repo** -> choose this repository (branch `main`).
2. **Variables**: add `CV_ADMIN_PASSWORD` (a long secret). Production mode turns on automatically.
3. **Volume**: right-click the service -> **Attach volume**, mount path `/data`. The database is
   stored there automatically, so complaints survive redeploys. Without a volume, the logs warn
   that data will be lost on every deploy.
4. **Settings -> Networking -> Generate Domain** to get a public HTTPS URL.
5. **Auto-deploy**: in **Settings -> Source**, keep the `main` branch connected (every push
   deploys). Turn on **Wait for CI** so a push only deploys after the GitHub Actions tests
   (`.github/workflows/ci.yml`) pass.

The first deploy takes a few minutes (PyTorch install). Sign in to `/admin` with
`CV_ADMIN_EMAIL` / `CV_ADMIN_PASSWORD`. A production database starts empty: no demo complaints
or demo citizens.

## Security

* Passwords: PBKDF2-SHA256 (120,000 iterations, per-user salt), constant-time comparison.
* Sessions: random bearer tokens that expire (`CV_TOKEN_TTL_HOURS`) and are revoked on sign-out.
* The front end builds every element with text nodes (`h()` in `static/js/common.js`), so
  citizen-supplied text is never parsed as HTML.
* A strict Content-Security-Policy (`script-src 'self'; style-src 'self'`) blocks any inline
  script or style. A test checks that the pages contain none.
* Public tracking shows status and timeline only; complaint text is visible only to the
  citizen who filed it and to officers.
* Input validation (LGA list, phone format, message length) and security headers.

## Tests

```bash
python -m pytest -q                              # API, chatbot flow, security, NLP and production-config tests
python functional_tests.py http://localhost:8000 # 15 functional test cases against a live, freshly seeded server
```

## Project layout

```
app/config.py            environment-based settings
app/dataset.py           grievance corpus builder (Nigerian English / Pidgin): train, dev, test
app/nlp.py               cleaning, lexicons, model serving (blend + fallback), hybrid triage
app/train.py             baseline model comparison (NB, LR, SVM), evaluation and charts
app/finetune.py          30-minute fine-tuning pipeline
app/transformer_model.py multi-task MiniLM classifier (training + inference)
app/agent.py             the grievance agent (perceive-decide-act)
app/db.py                SQLite schema, agencies, password hashing
app/main.py              FastAPI routes
app/seed.py              demonstration data
static/*.html            page markup only (no inline scripts, styles or event handlers)
static/css/              base.css design system + one stylesheet per page
static/js/               common.js (DOM builder, API client, session) + one ES module per page
models/                  trained models
reports/                 metrics, confusion matrices, fine-tuning report and charts
tests/                   pytest suite
```
