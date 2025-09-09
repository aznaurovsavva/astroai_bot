import os
from dotenv import load_dotenv

load_dotenv()

BOT_TOKEN = os.getenv("BOT_TOKEN", "")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY", "")
ADMIN_ID=1084054813
TEST_MODE=True
GEMINI_API_KEY = os.getenv("GEMINI_API_KEY", "")
MISTRAL_API_KEY = os.getenv("MISTRAL_API_KEY", "")
PALM_VISION = os.getenv("PALM_VISION", "off").lower() == "on"
VISION_PROVIDER = os.getenv("VISION_PROVIDER", "mistral")
MISTRAL_VISION_MODEL = os.getenv("MISTRAL_VISION_MODEL", "pixtral-12b")





# В будущем добавим иные настройки (Stars/платежи, провайдеры LLM и т.д.)
