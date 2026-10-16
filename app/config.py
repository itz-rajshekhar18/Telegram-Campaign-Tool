import os
from dotenv import load_dotenv

load_dotenv()

# Telegram API (get from https://my.telegram.org)
API_ID = int(os.getenv("API_ID") or "0")
API_HASH = os.getenv("API_HASH", "")

# Database
DATABASE_URL = "sqlite+aiosqlite:///./data/campaigns.db"

# Server
HOST = "0.0.0.0"
PORT = 8000

# Rate limit profiles (seconds between actions, daily caps)
RATE_PROFILES = {
    "safe": {
        "join_delay": (180, 420),      # 3–7 min
        "message_delay": (45, 120),
        "daily_joins": 15,
        "daily_messages": 60,
    },
    "balanced": {
        "join_delay": (90, 240),
        "message_delay": (25, 70),
        "daily_joins": 40,
        "daily_messages": 150,
    },
    "aggressive": {
        "join_delay": (30, 90),
        "message_delay": (10, 30),
        "daily_joins": 80,
        "daily_messages": 300,
    },
}

# Ollama
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "llama3.1:8b")

# Paths
SESSIONS_DIR = "./sessions"
DATA_DIR = "./data"
LOGS_DIR = "./logs"
