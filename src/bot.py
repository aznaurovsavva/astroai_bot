import logging
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters
)
from .config import BOT_TOKEN

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("astro-num-bot")

# --- Цены в Stars (XTR). Эквиваленты в тексте описания. ---
PRICE_NUM   = 200   # ~200 ₽
PRICE_PALM  = 300   # ~300 ₽
PRICE_NATAL = 500   # ~500 ₽

# Главное меню с оплатой
MENU = [
    [InlineKeyboardButton("🔢 Нумерология — оплатить", callback_data="buy_num")],
    [InlineKeyboardButton("🪬 Хиромантия — оплатить", callback_data="buy_palm")],
    [InlineKeyboardButton("🌌 Наталка PRO — оплатить", callback_data="buy_natal")],
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "Привет! Я делаю астрологические и нумерологические разборы 🔮\nВыбери услугу для оплаты:",
        reply_markup=InlineKeyboardMarkup(MENU),
    )

# Универсальная отправка инвойса в Stars
async def send_stars_invoice(
    update_or_query, context: ContextTypes.DEFAULT_TYPE,
    title: str, desc: str, payload: str, amount_stars: int
):
    chat_id = (
        update_or_query.effective_chat.id
        if isinstance(update_or_query, Update)
        else update_or_query.message.chat.id
        if hasattr(update_or_query, "message")
        else update_or_query.from_user.id
    )
    prices = [LabeledPrice(label=title, amount=amount_stars)]  # для XTR amount = кол-во звёзд
    await context.bot.send_invoice(
        chat_id=chat_id,
        title=title,
        description=desc,
        payload=payload,           # вернётся в successful_payment
        provider_token="",         # для Telegram Stars оставляем пустым
        currency="XTR",            # ключевой момент!
        prices=prices,
        start_parameter="buy",
        need_name=False,
        need_phone_number=False,
        need_email=False,
        need_shipping_address=False,
        is_flexible=False,
    )

async def on_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    q = update.callback_query
    await q.answer()
    if q.data == "buy_num":
        await send_stars_invoice(
            q, context,
            "Нумерология",
            "Краткий нумерологический разбор (≈ 200 ₽).",
            "NUM_200", PRICE_NUM
        )
    elif q.data == "buy_palm":
        await send_stars_invoice(
            q, context,
            "Хиромантия",
            "Разбор по фото ладони (≈ 300 ₽).",
            "PALM_300", PRICE_PALM
        )
    elif q.data == "buy_natal":
        await send_stars_invoice(
            q, context,
            "Наталка PRO",
            "Натальная карта + дома + аспекты + нумерология (≈ 500 ₽).",
            "NATAL_500", PRICE_NATAL
        )
    else:
        await q.edit_message_text("Выбирай услугу для оплаты ⤴️")

# Обязательный pre-checkout (здесь можно вставить валидации)
async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

# Успешная оплата
async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    payload = sp.invoice_payload  # например "NATAL_500"
    # TODO: здесь запишем заказ в БД (tg_id, payload, сумма, charge_id)
    charge_id = sp.telegram_payment_charge_id
    log.info(f"Payment ok: payload={payload}, charge_id={charge_id}, amount={sp.total_amount} XTR")

    mapping = {
        "NUM_200":   "Оплата получена ✅ Пришли ФИО и дату рождения в формате: 21.09.1999 Иван Иванов",
        "PALM_300":  "Оплата получена ✅ Пришли фото ладони (правой руки) при хорошем освещении.",
        "NATAL_500": "Оплата получена ✅ Напиши: дата, время (если знаешь) и город рождения.",
    }
    await update.message.reply_text(mapping.get(payload, "Оплата получена ✅"))

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN в окружении. Добавь его в .env или Railway Variables.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CallbackQueryHandler(on_menu))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    log.info("Bot is starting with long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()