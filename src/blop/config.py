import logging
import os
from pathlib import Path

# Suppress all logging before any imports
logging.disable(logging.CRITICAL)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "CRITICAL")

# Load .env from the repo root (two levels up from this file: src/blop/config.py → repo root)
try:
    from dotenv import load_dotenv
    _env_path = Path(__file__).parent.parent.parent / ".env"
    if _env_path.exists():
        load_dotenv(_env_path, override=False)  # override=False: explicit env vars take precedence
except Exception:
    pass

GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")
LOGIN_URL: str = os.getenv("LOGIN_URL", "")

TEST_USERNAME: str = os.getenv("TEST_USERNAME", "")
TEST_PASSWORD: str = os.getenv("TEST_PASSWORD", "")
STORAGE_STATE_PATH: str = os.getenv("STORAGE_STATE_PATH", "")
COOKIE_JSON_PATH: str = os.getenv("COOKIE_JSON_PATH", "")

BLOP_DB_PATH: str = os.getenv("BLOP_DB_PATH", ".blop/runs.db")
BLOP_HEADLESS: bool = os.getenv("BLOP_HEADLESS", "true").lower() == "true"
BLOP_MAX_STEPS: int = int(os.getenv("BLOP_MAX_STEPS", "50"))
