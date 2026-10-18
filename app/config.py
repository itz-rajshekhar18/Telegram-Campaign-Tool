import os
from pathlib import Path
from dotenv import load_dotenv

PROJECT_DIR = Path(__file__).resolve().parent.parent
load_dotenv(PROJECT_DIR / ".env")

# Telegram API (get from https://my.telegram.org)
API_ID = int(os.getenv("API_ID") or "0")
API_HASH = os.getenv("API_HASH", "")
TELEGRAM_BOT_TOKEN = os.getenv("TELEGRAM_BOT_TOKEN", "")
APP_ENV = os.getenv("APP_ENV", "development").lower()

# Database
DATABASE_URL = os.getenv("DATABASE_URL", f"sqlite+aiosqlite:///{(PROJECT_DIR / 'data' / 'campaigns.db').as_posix()}")

# Server
HOST = os.getenv("HOST", "127.0.0.1" if APP_ENV == "development" else "0.0.0.0")
PORT = int(os.getenv("PORT") or "8000")
TELEGRAM_API_BASE_URL = os.getenv("TELEGRAM_API_BASE_URL", "https://api.telegram.org").rstrip("/")
SEND_INTERVAL_SECONDS = float(os.getenv("SEND_INTERVAL_SECONDS") or "1.2")
DIRECT_SEND_MAX_RECIPIENTS = int(os.getenv("DIRECT_SEND_MAX_RECIPIENTS") or "20")
if not 1 <= SEND_INTERVAL_SECONDS <= 60:
    raise ValueError("SEND_INTERVAL_SECONDS must be between 1 and 60")
if not 1 <= DIRECT_SEND_MAX_RECIPIENTS <= 20:
    raise ValueError("DIRECT_SEND_MAX_RECIPIENTS must be between 1 and 20")

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Paths
SESSIONS_DIR = "./sessions"
DATA_DIR = "./data"
LOGS_DIR = "./logs"
