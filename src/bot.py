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

IMG_NUM = "https://source.unsplash.com/featured/1200x800/?numbers,geometry"
IMG_PALM = "https://source.unsplash.com/featured/1200x800/?palm,hand"
IMG_NATAL = "https://source.unsplash.com/featured/1200x800/?night,stars,astrology"

# Главное меню без слова "оплатить"
MENU = [
    [InlineKeyboardButton("🔢 Нумерология", callback_data="num")],
    [InlineKeyboardButton("🪬 Хиромантия", callback_data="palm")],
    [InlineKeyboardButton("🌌 Наталка PRO", callback_data="natal")],
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intro = (
        "✨ Добро пожаловать в *AstroAi* ✨\n\n"
        "Я — твой проводник в мир звёзд, чисел и линий судьбы. Здесь всё максимально просто: выбираешь направление — и я собираю для тебя персональный разбор.\n\n"
        "Что доступно сейчас:\n"
        "• 🔢 *Нумерология* — код твоей личности и предназначения по дате и имени.\n"
        "• 🪬 *Хиромантия* — разбор по фото ладони: таланты, риски, внутренние циклы.\n"
        "• 🌌 *Наталка PRO* — планеты, дома, аспекты + нумерология в одном портрете.\n\n"
        "Выбери направление ниже — расскажу подробнее и предложу оформить доступ."
    )
    await update.message.reply_text(intro, reply_markup=InlineKeyboardMarkup(MENU), parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intro = (
        "✨ Добро пожаловать в *AstroAi* ✨\n\n"
        "Я — твой проводник в мир звёзд, чисел и линий судьбы. Здесь всё максимально просто: выбираешь направление — и я собираю для тебя персональный разбор.\n\n"
        "Что доступно сейчас:\n"
        "• 🔢 *Нумерология* — код твоей личности и предназначения по дате и имени.\n"
        "• 🪬 *Хиромантия* — разбор по фото ладони: таланты, риски, внутренние циклы.\n"
        "• 🌌 *Наталка PRO* — планеты, дома, аспекты + нумерология в одном портрете.\n\n"
        "Выбери направление ниже — расскажу подробнее и предложу оформить доступ."
    )
    await update.message.reply_text(intro, reply_markup=InlineKeyboardMarkup(MENU), parse_mode="Markdown")

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
        caption_num = (
            "🔢 *Нумерология*\n\n"
            "Числа — это язык, на котором Вселенная шепчет о наших дарах и уроках. Я рассчитаю ключевые числа (судьбы, души, личности, имени) и разложу по полочкам: сильные стороны, зоны роста и практические шаги.\n\n"
            "Что ты получишь:\n"
            "• Краткий портрет на 3–4 абзаца;\n"
            "• Разбор каждого числа;\n"
            "• Рекомендации на месяц.\n\n"
            "Стоимость: *90 ⭐* (≈ 200 ₽)."
        )
        await q.message.reply_photo(
            photo=IMG_NUM,
            caption=caption_num,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Оплатить 90 ⭐", callback_data="buy_num")],
                [InlineKeyboardButton("← Назад", callback_data="back_home")],
            ]),
            parse_mode="Markdown",
        )
    elif q.data == "palm":
        caption_palm = (
            "🪬 *Хиромантия*\n\n"
            "Ладонь — живой дневник судьбы. По фото правой руки я рассмотрю линии сердца, головы и жизни, холмы и общий рисунок, чтобы мягко подсветить твои таланты и текущие вызовы.\n\n"
            "Что нужно от тебя: одно чёткое фото ладони при хорошем светe.\n\n"
            "Что ты получишь: образный разбор на 3–5 абзацев + практические советы.\n\n"
            "Стоимость: *130 ⭐* (≈ 300 ₽)."
        )
        await q.message.reply_photo(
            photo=IMG_PALM,
            caption=caption_palm,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Оплатить 130 ⭐", callback_data="buy_palm")],
                [InlineKeyboardButton("← Назад", callback_data="back_home")],
            ]),
            parse_mode="Markdown",
        )
    elif q.data == "natal":
        caption_natal = (
            "🌌 *Наталка PRO*\n\n"
            "Твой личный небесный атлас: планеты, знаки, *дома* и ключевые *аспекты* + нумерологический штрих-код. Отдельно отмечу ресурсы, риски и мягкие рекомендации на ближайший цикл.\n\n"
            "Что понадобится: дата, город и — по возможности — точное время рождения.\n\n"
            "Результат: структурированный текст 6–10 абзацев.\n\n"
            "Стоимость: *220 ⭐* (≈ 500 ₽)."
        )
        await q.message.reply_photo(
            photo=IMG_NATAL,
            caption=caption_natal,
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("Оплатить 220 ⭐", callback_data="buy_natal")],
                [InlineKeyboardButton("← Назад", callback_data="back_home")],
            ]),
            parse_mode="Markdown",
        )
    elif q.data == "back_home":
        try:
            await q.message.delete()
        except Exception:
            pass
        intro = (
            "✨ Добро пожаловать в *AstroAi* ✨\n\n"
            "Я — твой проводник в мир звёзд, чисел и линий судьбы. Здесь всё максимально просто: выбираешь направление — и я собираю для тебя персональный разбор.\n\n"
            "Что доступно сейчас:\n"
            "• 🔢 *Нумерология* — код твоей личности и предназначения по дате и имени.\n"
            "• 🪬 *Хиромантия* — разбор по фото ладони: таланты, риски, внутренние циклы.\n"
            "• 🌌 *Наталка PRO* — планеты, дома, аспекты + нумерология в одном портрете.\n\n"
            "Выбери направление ниже — расскажу подробнее и предложу оформить доступ."
        )
        await q.message.chat.send_message(
            intro,
            reply_markup=InlineKeyboardMarkup(MENU),
            parse_mode="Markdown",
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
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CallbackQueryHandler(on_menu))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))

    log.info("Bot is starting with long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()