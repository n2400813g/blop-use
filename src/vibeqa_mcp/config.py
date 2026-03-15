import logging
import os

# Suppress all logging before any imports
logging.disable(logging.CRITICAL)
os.environ.setdefault("ANONYMIZED_TELEMETRY", "false")
os.environ.setdefault("BROWSER_USE_LOGGING_LEVEL", "CRITICAL")

GOOGLE_API_KEY: str = os.getenv("GOOGLE_API_KEY", "")
APP_BASE_URL: str = os.getenv("APP_BASE_URL", "")
LOGIN_URL: str = os.getenv("LOGIN_URL", "")

TEST_USERNAME: str = os.getenv("TEST_USERNAME", "")
TEST_PASSWORD: str = os.getenv("TEST_PASSWORD", "")
STORAGE_STATE_PATH: str = os.getenv("STORAGE_STATE_PATH", "")
COOKIE_JSON_PATH: str = os.getenv("COOKIE_JSON_PATH", "")

VIBEQA_DB_PATH: str = os.getenv("VIBEQA_DB_PATH", ".vibetest/runs.db")
VIBEQA_HEADLESS: bool = os.getenv("VIBEQA_HEADLESS", "true").lower() == "true"
VIBEQA_MAX_STEPS: int = int(os.getenv("VIBEQA_MAX_STEPS", "50"))
