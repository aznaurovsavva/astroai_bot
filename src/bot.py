import logging
import re
from datetime import datetime
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters
)
from .config import BOT_TOKEN, TEST_MODE

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("astro-num-bot")

if TEST_MODE:
    log.warning("[TEST MODE] Payments are disabled. Using simulated purchases.")

# --- Цены в Stars (XTR). Эквиваленты в тексте описания. ---
PRICE_NUM   = 90   # ~200 ₽
PRICE_PALM  = 130   # ~300 ₽
PRICE_NATAL = 220   # ~500 ₽

# Состояния диалога для Наталки PRO
NATAL_DATE = "natal_date"
NATAL_TIME = "natal_time"
NATAL_CITY = "natal_city"


async def send_service_text(q, caption: str, buy_cbdata: str, buy_label: str):
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(buy_label, callback_data=buy_cbdata)],
        [InlineKeyboardButton("← Назад", callback_data="back_home")],
    ])
    try:
        await q.edit_message_text(caption, reply_markup=kb, parse_mode="Markdown")
    except Exception:
        # Если исходное сообщение нельзя редактировать (или было фото), отправим новым
        await q.message.chat.send_message(caption, reply_markup=kb, parse_mode="Markdown")

# Главное меню без слова "оплатить"
MENU = [
    [InlineKeyboardButton("🔢 Нумерология", callback_data="num")],
    [InlineKeyboardButton("🪬 Хиромантия", callback_data="palm")],
    [InlineKeyboardButton("🌌 Натальная карта Pro", callback_data="natal")],
]

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intro = (
        "✨ Добро пожаловать в *AstroMagic* ✨\n\n"
        "Мы — команда практикующих астрологов, нумерологов и исследователей эзотерики.\n"
        "Наша цель — сделать глубокие знания о звёздах, числах и линиях судьбы доступными каждому.\n\n"
        "Каждый разбор создаётся с вниманием к деталям, с опорой на классические школы и современные методы. "
        "Вы получаете не просто сухую интерпретацию, а образное и структурированное объяснение того, что скрыто "
        "в вашей дате рождения, натальной карте или линиях ладони.\n\n"
        "🔮 Что мы предлагаем:\n"
        "• *Нумерология* — ваш уникальный код личности и предназначения.\n"
        "• *Хиромантия* — чтение линий судьбы по фото ладони.\n"
        "• *Натальная карта Pro* — комплексный астрологический разбор: планеты, дома, аспекты + нумерология.\n\n"
        "Выберите направление ниже, и мы подготовим для вас персональный разбор с рекомендациями."
    )
    if TEST_MODE:
        intro += "\n\n_Сейчас включён тестовый режим: оплата отключена, доступ выдаётся для проверки флоу._"
    await update.message.reply_text(intro, reply_markup=InlineKeyboardMarkup(MENU), parse_mode="Markdown")

async def menu_cmd(update: Update, context: ContextTypes.DEFAULT_TYPE):
    intro = (
        "✨ Добро пожаловать в *AstroMagic* ✨\n\n"
        "Мы — команда практикующих астрологов, нумерологов и исследователей эзотерики.\n"
        "Наша цель — сделать глубокие знания о звёздах, числах и линиях судьбы доступными каждому.\n\n"
        "Каждый разбор создаётся с вниманием к деталям, с опорой на классические школы и современные методы. "
        "Вы получаете не просто сухую интерпретацию, а образное и структурированное объяснение того, что скрыто "
        "в вашей дате рождения, натальной карте или линиях ладони.\n\n"
        "🔮 Что мы предлагаем:\n"
        "• *Нумерология* — ваш уникальный код личности и предназначения.\n"
        "• *Хиромантия* — чтение линий судьбы по фото ладони.\n"
        "• *Натальная карта Pro* — комплексный астрологический разбор: планеты, дома, аспекты + нумерология.\n\n"
        "Выберите направление ниже, и мы подготовим для вас персональный разбор с рекомендациями."
    )
    if TEST_MODE:
        intro += "\n\n_Сейчас включён тестовый режим: оплата отключена, доступ выдаётся для проверки флоу._"
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
        await send_service_text(q, caption_num, "buy_num", "Оплатить 90 ⭐")
    elif q.data == "palm":
        caption_palm = (
            "🪬 *Хиромантия*\n\n"
            "Ладонь — живой дневник судьбы. По фото правой руки я рассмотрю линии сердца, головы и жизни, холмы и общий рисунок, чтобы мягко подсветить твои таланты и текущие вызовы.\n\n"
            "Что нужно от тебя: одно чёткое фото ладони при хорошем светe.\n\n"
            "Что ты получишь: образный разбор на 3–5 абзацев + практические советы.\n\n"
            "Стоимость: *130 ⭐* (≈ 300 ₽)."
        )
        await send_service_text(q, caption_palm, "buy_palm", "Оплатить 130 ⭐")
    elif q.data == "natal":
        caption_natal = (
            "🌌 *Натальная карта Pro*\n\n"
            "Твой личный небесный атлас: планеты, знаки, *дома* и ключевые *аспекты* + нумерологический штрих-код. Отдельно отмечу ресурсы, риски и мягкие рекомендации на ближайший цикл.\n\n"
            "Что понадобится: дата, город и — по возможности — точное время рождения.\n\n"
            "Результат: структурированный текст 6–10 абзацев.\n\n"
            "Стоимость: *220 ⭐* (≈ 500 ₽)."
        )
        await send_service_text(q, caption_natal, "buy_natal", "Оплатить 220 ⭐")
    elif q.data == "back_home":
        try:
            await q.message.delete()
        except Exception:
            pass
        intro = (
            "✨ Добро пожаловать в *AstroMagic* ✨\n\n"
            "Мы — команда практикующих астрологов, нумерологов и исследователей эзотерики.\n"
            "Наша цель — сделать глубокие знания о звёздах, числах и линиях судьбы доступными каждому.\n\n"
            "Каждый разбор создаётся с вниманием к деталям, с опорой на классические школы и современные методы. "
            "Вы получаете не просто сухую интерпретацию, а образное и структурированное объяснение того, что скрыто "
            "в вашей дате рождения, натальной карте или линиях ладони.\n\n"
            "🔮 Что мы предлагаем:\n"
            "• *Нумерология* — ваш уникальный код личности и предназначения.\n"
            "• *Хиромантия* — чтение линий судьбы по фото ладони.\n"
            "• *Натальная карта Pro* — комплексный астрологический разбор: планеты, дома, аспекты + нумерология.\n\n"
            "Выберите направление ниже, и мы подготовим для вас персональный разбор с рекомендациями."
        )
        if TEST_MODE:
            intro += "\n\n_Сейчас включён тестовый режим: оплата отключена, доступ выдаётся для проверки флоу._"
        await q.message.chat.send_message(
            intro,
            reply_markup=InlineKeyboardMarkup(MENU),
            parse_mode="Markdown",
        )
    elif q.data == "buy_num":
        if TEST_MODE:
            await _begin_flow_after_payment("NUM_200", update, context)
        else:
            await send_stars_invoice(
                q, context,
                "Нумерология",
                "Краткий нумерологический разбор (≈ 200 ₽).",
                "NUM_200", PRICE_NUM
            )
    elif q.data == "buy_palm":
        if TEST_MODE:
            await _begin_flow_after_payment("PALM_300", update, context)
        else:
            await send_stars_invoice(
                q, context,
                "Хиромантия",
                "Разбор по фото ладони (≈ 300 ₽).",
                "PALM_300", PRICE_PALM
            )
    elif q.data == "buy_natal":
        if TEST_MODE:
            await _begin_flow_after_payment("NATAL_500", update, context)
        else:
            await send_stars_invoice(
                q, context,
                "Натальная карта Pro",
                "Натальная карта + дома + аспекты + нумерология (≈ 500 ₽).",
                "NATAL_500", PRICE_NATAL
            )
    else:
        await q.edit_message_text("Выбирай услугу ⤴️")

# Обязательный pre-checkout (здесь можно вставить валидации)
async def precheckout_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    await update.pre_checkout_query.answer(ok=True)

async def _begin_flow_after_payment(payload: str, update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Starts the appropriate dialog flow as if payment succeeded."""
    if payload == "NATAL_500":
        ud = context.user_data
        ud.clear()
        ud["flow"] = "natal"
        ud["state"] = NATAL_DATE
        await update.effective_chat.send_message(
            "Оплата получена ✅\n\nДавай начнём разбор.\n"
            "1) Напиши дату рождения в формате ДД.ММ.ГГГГ\n\n"
            "Пример: 21.09.1999"
        )
        return

    if payload == "PALM_300":
        ud = context.user_data
        ud.clear()
        ud["flow"] = "palm"
        # В будущем добавим проверку фото; пока просто просим фото
        await update.effective_chat.send_message(
            "Оплата получена ✅\n\nПришли *одно чёткое фото правой ладони* при хорошем освещении.",
            parse_mode="Markdown",
        )
        return

    if payload == "NUM_200":
        ud = context.user_data
        ud.clear()
        ud["flow"] = "num"
        await update.effective_chat.send_message(
            "Оплата получена ✅\n\nНапиши *дату рождения и ФИО одной строкой* в формате:\n"
            "`ДД.ММ.ГГГГ Имя Фамилия`\n\nНапример: `21.09.1999 Иван Иванов`",
            parse_mode="Markdown",
        )
        return

    await update.effective_chat.send_message("Оплата получена ✅")

async def successful_payment_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    sp = update.message.successful_payment
    payload = sp.invoice_payload
    charge_id = sp.telegram_payment_charge_id
    log.info(f"Payment ok: payload={payload}, charge_id={charge_id}, amount={sp.total_amount} XTR")
    await _begin_flow_after_payment(payload, update, context)

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    ud = context.user_data

    # Если нет активного потока — выходим
    if ud.get("flow") != "natal":
        return

    state = ud.get("state")

    # ШАГ 1 — ДАТА
    if state == NATAL_DATE:
        if not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
            await update.message.reply_text("Пожалуйста, укажи дату в формате ДД.ММ.ГГГГ. Например: 07.03.1995")
            return
        try:
            _ = datetime.strptime(text, "%d.%m.%Y")
        except ValueError:
            await update.message.reply_text("Похоже, дата некорректна. Проверь, пожалуйста, и пришли ещё раз.")
            return
        ud["natal_date"] = text
        ud["state"] = NATAL_TIME
        await update.message.reply_text(
            "Отлично! Теперь укажи *время рождения* в формате ЧЧ:ММ.\n"
            "Если не знаешь точное время — напиши ‘не знаю’.",
            parse_mode="Markdown",
        )
        return

    # ШАГ 2 — ВРЕМЯ
    if state == NATAL_TIME:
        low = text.lower()
        if low in ("не знаю", "неизвестно", "нет", "-"):
            ud["natal_time"] = None
        else:
            if not re.fullmatch(r"\d{1,2}:\d{2}", text):
                await update.message.reply_text("Время укажи так: ЧЧ:ММ (например, 14:25) или напиши ‘не знаю’.")
                return
            hh, mm = map(int, text.split(":"))
            if not (0 <= hh <= 23 and 0 <= mm <= 59):
                await update.message.reply_text("Проверь часы и минуты (0–23 и 0–59). Пришли время ещё раз.")
                return
            ud["natal_time"] = f"{hh:02d}:{mm:02d}"
        ud["state"] = NATAL_CITY
        await update.message.reply_text(
            "И последний шаг: *город рождения* (можно со страной/областью для точности).\n"
            "Например: ‘Омск, Россия’ или ‘Almaty, Kazakhstan’.",
            parse_mode="Markdown",
        )
        return

    # ШАГ 3 — ГОРОД
    if state == NATAL_CITY:
        if len(text) < 2:
            await update.message.reply_text("Нужно назвать населённый пункт. Пришли, пожалуйста, ещё раз.")
            return
        ud["natal_city"] = text

        date_str = ud.get("natal_date")
        time_str = ud.get("natal_time") or "неизвестно"
        city_str = ud.get("natal_city")

        # Сбросим состояние
        ud["flow"] = None
        ud["state"] = None

        await update.message.reply_text(
            "Спасибо! Я записал данные для Наталки PRO:\n\n"
            f"• Дата: *{date_str}*\n"
            f"• Время: *{time_str}*\n"
            f"• Город: *{city_str}*\n\n"
            "На следующем шаге подключим точные расчёты и пришлём разбор.",
            parse_mode="Markdown",
        )
        return

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN в окружении. Добавь его в .env или Railway Variables.")

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CallbackQueryHandler(on_menu))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    log.info("Bot is starting with long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()