"""Runtime configuration, read from environment variables with safe defaults.

Environments
------------
development  (default locally)  API docs at /docs
production   (default on Railway, or CV_ENV=production)
             API docs are hidden, HSTS is sent
"""
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Railway sets RAILWAY_ENVIRONMENT_NAME on every deployment.
ENV = (os.getenv("CV_ENV") or ("production" if os.getenv("RAILWAY_ENVIRONMENT_NAME") else "development")).lower()
IS_PRODUCTION = ENV == "production"

# SQLite file. On Railway, attach a volume and the database is kept on it automatically
# (Railway exposes the mount point as RAILWAY_VOLUME_MOUNT_PATH); otherwise it would be
# lost on every redeploy.
_VOLUME = os.getenv("RAILWAY_VOLUME_MOUNT_PATH")
DB_PATH = Path(os.getenv("CV_DB_PATH") or (Path(_VOLUME) / "citizenvoice.db" if _VOLUME else ROOT / "data" / "citizenvoice.db"))
DB_ON_VOLUME = bool(_VOLUME) or bool(os.getenv("CV_DB_PATH"))

MODEL_DIR = Path(os.getenv("CV_MODEL_DIR", ROOT / "models"))
REPORT_DIR = Path(os.getenv("CV_REPORT_DIR", ROOT / "reports"))

# Sign-in sessions expire after this many hours (default one week).
TOKEN_TTL_HOURS = int(os.getenv("CV_TOKEN_TTL_HOURS", "168"))

# Set CV_USE_TRANSFORMER=0 to serve only the classical TF-IDF models (no PyTorch needed).
USE_TRANSFORMER = os.getenv("CV_USE_TRANSFORMER", "1") != "0"

# Officer accounts are never created automatically. The first officer to register becomes
# the super administrator; later officers wait for the super administrator's approval.
MIN_OFFICER_PASSWORD_LENGTH = 8

LOG_LEVEL = os.getenv("CV_LOG_LEVEL", "INFO").upper()

# Web server (python -m app.serve)
HOST = os.getenv("HOST", "0.0.0.0")
PORT = int(os.getenv("PORT", "8000"))

MAX_MESSAGE_CHARS = 2000
