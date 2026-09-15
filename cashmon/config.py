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

# Used only as a conversational fallback when a message doesn't match any of
# the rigid text formats (see cashmon/bot/nlu.py) -- optional. Without it,
# the bot still works, just without free-form phrasing.
ANTHROPIC_API_KEY = os.environ.get("ANTHROPIC_API_KEY")
NLU_MODEL = os.environ.get("NLU_MODEL", "claude-haiku-4-5")
