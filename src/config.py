import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
TEST_MODE = os.getenv("TEST_MODE", "false").strip().lower() in {"1", "true", "yes", "on"}
try:
    ADMIN_ID = int(os.getenv("ADMIN_ID", "0").strip() or 0)
except Exception:
    ADMIN_ID = 0

# В будущем добавим иные настройки (Stars/платежи, провайдеры LLM и т.д.)
