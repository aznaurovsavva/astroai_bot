import logging
import re
from datetime import datetime
import os, json, sqlite3
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters
)
from .config import BOT_TOKEN, TEST_MODE, ADMIN_ID

logging.basicConfig(
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    level=logging.INFO,
)
log = logging.getLogger("astro-num-bot")

if TEST_MODE:
    log.warning("[TEST MODE] Payments are disabled. Using simulated purchases.")

log.info(f"ADMIN_ID set to: {ADMIN_ID}")

# --- Simple SQLite storage (profiles + orders) ---
DB_PATH = os.getenv("DB_PATH", "data.sqlite3")

def _conn():
  return sqlite3.connect(DB_PATH)

def init_db():
  con = _conn(); cur = con.cursor()
  cur.execute("""
    CREATE TABLE IF NOT EXISTS profiles(
      user_id    INTEGER PRIMARY KEY,
      full_name  TEXT,
      username   TEXT,
      lang       TEXT,
      created_at TEXT,
      last_seen  TEXT
    )
  """)
  cur.execute("""
    CREATE TABLE IF NOT EXISTS orders(
      id            INTEGER PRIMARY KEY AUTOINCREMENT,
      user_id       INTEGER,
      payload       TEXT,
      amount_stars  INTEGER,
      status        TEXT,
      charge_id     TEXT,
      meta_json     TEXT,
      created_at    TEXT,
      updated_at    TEXT
    )
  """)
  con.commit(); con.close()

def upsert_profile(user_id: int, full_name: str = "", username: str = "", lang: str = ""):
  now = datetime.utcnow().isoformat()
  con = _conn(); cur = con.cursor()
  cur.execute("SELECT user_id FROM profiles WHERE user_id=?", (user_id,))
  row = cur.fetchone()
  if row:
    cur.execute("""UPDATE profiles
                   SET full_name=?, username=?, lang=?, last_seen=?
                   WHERE user_id=?""",
                (full_name, username, lang, now, user_id))
  else:
    cur.execute("""INSERT INTO profiles(user_id, full_name, username, lang, created_at, last_seen)
                   VALUES(?,?,?,?,?,?)""",
                (user_id, full_name, username, lang, now, now))
  con.commit(); con.close()

def create_order(user_id: int, payload: str, amount_stars: int, status: str = "awaiting_input", charge_id: str | None = None, meta: dict | None = None) -> int:
  now = datetime.utcnow().isoformat()
  con = _conn(); cur = con.cursor()
  cur.execute("""INSERT INTO orders(user_id, payload, amount_stars, status, charge_id, meta_json, created_at, updated_at)
                 VALUES(?,?,?,?,?,?,?,?)""",
              (user_id, payload, amount_stars, status, charge_id, json.dumps(meta or {}, ensure_ascii=False), now, now))
  oid = cur.lastrowid
  con.commit(); con.close()
  return oid

def update_order(order_id: int, *, status: str | None = None, meta_merge: dict | None = None, charge_id: str | None = None):
  con = _conn(); cur = con.cursor()
  cur.execute("SELECT meta_json FROM orders WHERE id=?", (order_id,))
  row = cur.fetchone()
  meta = {} if not row or not row[0] else json.loads(row[0])
  if meta_merge:
    meta.update(meta_merge)
  sets, params = [], []
  if status is not None:
    sets.append("status=?"); params.append(status)
  if charge_id is not None:
    sets.append("charge_id=?"); params.append(charge_id)
  sets.extend(["meta_json=?", "updated_at=?"])
  params.extend([json.dumps(meta, ensure_ascii=False), datetime.utcnow().isoformat(), order_id])
  cur.execute(f"UPDATE orders SET {', '.join(sets)} WHERE id=?", params)
  con.commit(); con.close()

# --- Helper to fetch recent orders ---
def fetch_last_orders(limit: int = 5):
    con = _conn(); cur = con.cursor()
    cur.execute(
        """
        SELECT id, user_id, payload, amount_stars, status, charge_id, created_at
        FROM orders
        ORDER BY id DESC
        LIMIT ?
        """,
        (limit,)
    )
    rows = cur.fetchall()
    con.close()
    return rows

# --- Цены в Stars (XTR). Эквиваленты в тексте описания. ---
PRICE_NUM   = 90   # ~200 ₽
PRICE_PALM  = 130   # ~300 ₽
PRICE_NATAL = 220   # ~500 ₽

# Состояния диалога для Наталки PRO
NATAL_DATE = "natal_date"
NATAL_TIME = "natal_time"
NATAL_CITY = "natal_city"

# Состояния для хиромантии и нумерологии
PALM_PHOTO = "palm_photo"
NUM_INPUT  = "num_input"

# Карта сумм для записи в заказы

AMOUNT_BY_PAYLOAD = {
    "NUM_200": PRICE_NUM,
    "PALM_300": PRICE_PALM,
    "NATAL_500": PRICE_NATAL,
}

# --- Нумерология: расчёт числа судьбы + короткие трактовки ---
NUM_DESCRIPTIONS = {
    1: "Лидерство, самостоятельность, импульс к началу.",
    2: "Дипломатия, партнёрство, чуткость.",
    3: "Коммуникация, творчество, выражение себя.",
    4: "Структура, дисциплина, надёжность.",
    5: "Свобода, перемены, путешествия, гибкость.",
    6: "Забота, семья, красота, ответственность.",
    7: "Аналитика, духовность, глубокие смыслы.",
    8: "Амбиции, ресурсы, управление и влияние.",
    9: "Служение, гуманизм, завершение циклов.",
    11: "Мастер-число интуиции и вдохновения.",
    22: "Мастер-число созидателя больших проектов.",
}

def calc_life_path_ddmmyyyy(date_str: str) -> int:
    digits = [int(ch) for ch in date_str if ch.isdigit()]
    s = sum(digits)
    def reduce(n: int) -> int:
        while n not in (11, 22) and n > 9:
            n = sum(int(c) for c in str(n))
        return n
    return reduce(s)


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
    u = update.effective_user
    upsert_profile(u.id, u.full_name or "", u.username or "", (u.language_code or ""))
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
    u = update.effective_user
    upsert_profile(u.id, u.full_name or "", u.username or "", (u.language_code or ""))
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


# --- Whoami command handler ---
async def whoami(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    await update.message.reply_text(
        f"your id: {u.id}\nADMIN_ID: {ADMIN_ID} (type={type(ADMIN_ID).__name__})\nTEST_MODE: {TEST_MODE}")


# --- Admin command: show last orders ---
async def orders_last(update: Update, context: ContextTypes.DEFAULT_TYPE):
    u = update.effective_user
    try:
        admin_id_val = int(ADMIN_ID)
    except Exception:
        admin_id_val = 0
    if admin_id_val and int(u.id) != admin_id_val:
        await update.message.reply_text("Недостаточно прав.")
        return

    # поддержка необязательного аргумента количества: /orders_last 10
    try:
        limit = int(context.args[0]) if context.args else 5
        limit = max(1, min(limit, 50))
    except Exception:
        limit = 5

    rows = fetch_last_orders(limit=limit)
    if not rows:
        await update.message.reply_text("Пока заказов нет.")
        return

    lines = ["Последние заказы:"]
    for (oid, uid, payload, amount, status, charge, created) in rows:
        date = created.split('T')[0] if created else ""
        lines.append(f"• #{oid} | user:{uid} | {payload} {amount}⭐ | {status} | {date}")
    await update.message.reply_text("\n".join(lines))

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

async def _begin_flow_after_payment(payload: str, update: Update, context: ContextTypes.DEFAULT_TYPE, charge_id: str | None = None):
    """Starts the appropriate dialog flow as if payment succeeded, and creates an order."""
    u = update.effective_user
    amount = AMOUNT_BY_PAYLOAD.get(payload, 0)
    order_id = create_order(u.id, payload, amount, status="awaiting_input", charge_id=charge_id)
    ud = context.user_data
    ud.clear()
    ud["order_id"] = order_id

    # Наталка PRO
    if payload == "NATAL_500":
        ud["flow"] = "natal"; ud["state"] = NATAL_DATE
        await update.effective_chat.send_message(
            "Оплата получена ✅\n\nДавай начнём разбор.\n"
            "1) Напиши дату рождения в формате ДД.ММ.ГГГГ\n\n"
            "Пример: 21.09.1999"
        )
        return

    # Хиромантия
    if payload == "PALM_300":
        ud["flow"] = "palm"; ud["state"] = PALM_PHOTO
        await update.effective_chat.send_message(
            "Оплата получена ✅\n\nПришли *одно чёткое фото правой ладони* при хорошем освещении.",
            parse_mode="Markdown",
        )
        return

    # Нумерология
    if payload == "NUM_200":
        ud["flow"] = "num"; ud["state"] = NUM_INPUT
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
    await _begin_flow_after_payment(payload, update, context, charge_id=charge_id)

async def text_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (update.message.text or "").strip()
    ud = context.user_data
    flow = ud.get("flow"); state = ud.get("state")
    if not flow:
        return

    # ---------- Наталка ----------
    if flow == "natal":
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

        if state == NATAL_CITY:
            if len(text) < 2:
                await update.message.reply_text("Нужно назвать населённый пункт. Пришли, пожалуйста, ещё раз.")
                return
            ud["natal_city"] = text

            # Закрываем заказ: статус done + мета
            order_id = ud.get("order_id")
            if order_id:
                update_order(order_id, status="done", meta_merge={
                    "natal_date": ud.get("natal_date"),
                    "natal_time": ud.get("natal_time"),
                    "natal_city": ud.get("natal_city"),
                })

            ud["flow"] = None; ud["state"] = None
            await update.message.reply_text(
                "Спасибо! Я записал данные для Наталки PRO:\n\n"
                f"• Дата: *{ud.get('natal_date')}*\n"
                f"• Время: *{ud.get('natal_time') or 'неизвестно'}*\n"
                f"• Город: *{ud.get('natal_city')}*\n\n"
                "На следующем шаге подключим точные расчёты и пришлём разбор.",
                parse_mode="Markdown",
            )
            return

    # ---------- Нумерология ----------
    if flow == "num" and state == NUM_INPUT:
        parts = text.split(maxsplit=1)
        if len(parts) < 2 or not re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", parts[0]):
            await update.message.reply_text(
                "Пожалуйста, укажи: `ДД.ММ.ГГГГ Имя Фамилия`.\nНапример: `07.03.1995 Анна Петрова`",
                parse_mode="Markdown",
            )
            return
        dob_str, full_name = parts[0], parts[1]
        try:
            _ = datetime.strptime(dob_str, "%d.%m.%Y")
        except ValueError:
            await update.message.reply_text("Дата выглядит некорректно. Проверь и пришли ещё раз.")
            return

        life_path = calc_life_path_ddmmyyyy(dob_str)
        meaning = NUM_DESCRIPTIONS.get(life_path, "Личный путь и опыт через число судьбы.")

        order_id = ud.get("order_id")
        if order_id:
            update_order(order_id, status="done", meta_merge={
                "num_dob": dob_str,
                "num_name": full_name,
                "life_path": life_path,
            })

        ud["flow"] = None; ud["state"] = None
        await update.message.reply_text(
            "**Нумерологический экспресс-разбор**\n\n"
            f"• Имя: *{full_name}*\n"
            f"• Дата рождения: *{dob_str}*\n"
            f"• Число судьбы: *{life_path}* — {meaning}\n\n"
            "Это краткая версия. Полный разбор с матрицей, кодами задач и рекомендациями добавим в ближайшее время.",
            parse_mode="Markdown",
        )
        return

# --- Photo router for palmistry ---
async def photo_router(update: Update, context: ContextTypes.DEFAULT_TYPE):
    ud = context.user_data
    if ud.get("flow") != "palm" or ud.get("state") != PALM_PHOTO:
        return
    photos = update.message.photo or []
    if not photos:
        return
    file_id = photos[-1].file_id

    order_id = ud.get("order_id")
    if order_id:
        update_order(order_id, status="done", meta_merge={"palm_photo_file_id": file_id})

    ud["flow"] = None; ud["state"] = None
    await update.message.reply_text("Фото получено ✅\n\nСкоро подготовим разбор по линиям ладони.")

def main():
    if not BOT_TOKEN:
        raise RuntimeError("Не найден BOT_TOKEN в окружении. Добавь его в .env или Railway Variables.")

    init_db()

    app = Application.builder().token(BOT_TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", menu_cmd))
    app.add_handler(CommandHandler("whoami", whoami))
    app.add_handler(CommandHandler("orders_last", orders_last))
    app.add_handler(CallbackQueryHandler(on_menu))
    app.add_handler(PreCheckoutQueryHandler(precheckout_handler))
    app.add_handler(MessageHandler(filters.SUCCESSFUL_PAYMENT, successful_payment_handler))
    app.add_handler(MessageHandler(filters.PHOTO, photo_router))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, text_router))

    log.info("Bot is starting with long polling...")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()