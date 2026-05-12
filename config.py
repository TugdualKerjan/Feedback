import os

DB_PATH = os.environ.get("DB_PATH", "marc.db")
BASE_URL = os.environ.get("BASE_URL", "https://feedback.tugdual.fr")
TIMEOUT = 15
SESSION_RATE_LIMIT = int(os.environ.get("SESSION_RATE_LIMIT", "5"))
SESSION_RATE_WINDOW = int(os.environ.get("SESSION_RATE_WINDOW", "60"))
