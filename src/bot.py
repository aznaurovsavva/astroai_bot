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
PRICE_NUM   = 90   # ~200 ₽
PRICE_PALM  = 130   # ~300 ₽
PRICE_NATAL = 220   # ~500 ₽

# Главное меню без слова "оплатить"
MENU = [
    [InlineKeyboardButton("🔢 Нумерология", callback_data="num")],
    [InlineKeyboardButton("🪬 Хиромантия", callback_data="palm")],
    [InlineKeyboardButton("🌌 Наталка PRO", callback_data="natal")],
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.message.reply_text(
        "✨ Добро пожаловать в AstroAi ✨\n\nЯ — твой личный проводник в мир звёзд, чисел и линий судьбы.\n\nЗдесь ты можешь:\n🔢 Получить нумерологический разбор по дате рождения\n🪬 Заглянуть в тайны своей ладони через хиромантию\n🌌 Узнать астрологическую натальную карту с домами и аспектами\n\nВыбери направление, которое откликается тебе прямо сейчас:",
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
    if q.data == "num":
        await q.edit_message_text(
            "🔢 *Нумерология*\n\nЧисла несут уникальный код твоей личности. Разбор покажет сильные и слабые стороны, предназначение и кармические уроки.\n\nСтоимость: 90 ⭐ (≈200 ₽)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Оплатить 90 ⭐", callback_data="buy_num")]
            ]),
            parse_mode="Markdown"
        )
    elif q.data == "palm":
        await q.edit_message_text(
            "🪬 *Хиромантия*\n\nЛинии на ладони — это твой живой дневник судьбы. Разбор по фото покажет таланты, вызовы и внутренний потенциал.\n\nСтоимость: 130 ⭐ (≈300 ₽)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Оплатить 130 ⭐", callback_data="buy_palm")]
            ]),
            parse_mode="Markdown"
        )
    elif q.data == "natal":
        await q.edit_message_text(
            "🌌 *Наталка PRO*\n\nПолный астрологический разбор: планеты, дома и аспекты, усиленный нумерологией. Это как персональная карта Вселенной для твоей жизни.\n\nСтоимость: 220 ⭐ (≈500 ₽)",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Оплатить 220 ⭐", callback_data="buy_natal")]
            ]),
            parse_mode="Markdown"
        )
    elif q.data == "buy_num":
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
        await q.edit_message_text("Выбирай услугу ⤴️")

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