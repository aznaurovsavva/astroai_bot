import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ADMIN_ID=1084054813
TEST_MODE=True





# В будущем добавим иные настройки (Stars/платежи, провайдеры LLM и т.д.)
