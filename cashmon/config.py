"""Environment-based configuration. Loads a .env file if python-dotenv is available."""
import os
from pathlib import Path

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass

BASE_DIR = Path(__file__).resolve().parent.parent
DB_PATH = os.environ.get("CASHMON_DB_PATH", str(BASE_DIR / "cashmon.db"))
TELEGRAM_BOT_TOKEN = os.environ.get("TELEGRAM_BOT_TOKEN")
OWNER_CHAT_ID = os.environ.get("OWNER_CHAT_ID")
