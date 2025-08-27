import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes

from .config import BOT_TOKEN

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("astro-num-bot")

MENU = [
    [InlineKeyboardButton("🔢 Нумерология", callback_data="num")],
    [InlineKeyboardButton("🪬 Хиромантия", callback_data="palm")],
    [InlineKeyboardButton("🌌 Наталка PRO", callback_data="natal")],
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я делаю астрологические и нумерологические разборы 🔮\nВыбери раздел:",
        reply_markup=InlineKeyboardMarkup(MENU),
    )

async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    mapping = {
        "num": "Нумерология скоро будет подключена. Продолжаем настройку ✅",
        "palm": "Хиромантия по фото скоро будет подключена. Продолжаем настройку ✅",
        "natal": "Наталка PRO скоро будет подключена. Продолжаем настройку ✅",
    }
    await q.edit_message_text(mapping.get(q.data, "Скоро добавим больше возможностей ✨"))

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN в окружении. Добавь его в .env или Railway Variables.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_menu))

    log.info("Bot is starting with long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
