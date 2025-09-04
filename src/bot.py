import logging
import re
from datetime import datetime
import os, json, sqlite3
import asyncio
import requests
from telegram import Update, InlineKeyboardMarkup, InlineKeyboardButton, LabeledPrice
from telegram.ext import (
    Application, CommandHandler, CallbackQueryHandler,
    ContextTypes, PreCheckoutQueryHandler, MessageHandler, filters
)
from .config import BOT_TOKEN, TEST_MODE, ADMIN_ID, OPENAI_API_KEY, GEMINI_API_KEY, MISTRAL_API_KEY

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

# --- LLM: Prompt builders for detailed numerology report ---
SYSTEM_PROMPT = (
    "Вы — команда AstroMagic: практикующие астрологи и нумерологи. "
    "Готовьте развёрнутый, художественно-эзотерический, но структурированный отчёт на русском, "
    "используя ТОЛЬКО переданные данные. Проверяйте согласованность и мягко отмечайте расхождения. "
    "Без фатализма и без медицинских/финансовых советов."
)

DEVELOPER_PROMPT = (
    "Правила вывода: верните СТРОГО один JSON-объект со структурой:\n"
    "{\n"
    "  \"title\": str,\n"
    "  \"summary\": str,\n"
    "  \"life_path\": {\"value\": int, \"meaning\": str, \"strengths\": [str], \"risks\": [str], \"advice\": [str]},\n"
    "  \"pythagoras_matrix\": {\n"
    "    \"grid_text\": str,\n"
    "    \"lines_overview\": [{\"axis\": str, \"total\": int, \"tone\": str, \"comment\": str}],\n"
    "    \"digits\": [{\"digit\": int, \"count\": int, \"meaning\": str, \"advice\": str}],\n"
    "    \"missing\": [int], \"dominant\": [int]\n"
    "  },\n"
    "  \"practical_recs\": {\"week\": [str], \"month\": [str], \"focus_areas\": [str]},\n"
    "  \"data_notes\": [str]\n"
    "}\n"
    "Никаких пояснений вне JSON."
)

def build_user_prompt_for_numerology(input_payload: dict) -> str:
  # Compose a deterministic, readable block the model will parse
  return (
    "Ниже — данные пользователя для нумерологического разбора. Проверьте согласованность и подготовьте JSON отчёт.\n\n"
    f"full_name: {input_payload.get('full_name','')}\n"
    f"dob_ddmmyyyy: {input_payload.get('dob_ddmmyyyy','')}\n"
    f"life_path: {input_payload.get('life_path','')}\n\n"
    "pythagoras_counts:\n" + json.dumps(input_payload.get('pythagoras_counts', {}), ensure_ascii=False) + "\n\n"
    "pythagoras_lines:\n" + json.dumps(input_payload.get('pythagoras_lines', {}), ensure_ascii=False) + "\n\n"
    "pythagoras_ext:\n" + json.dumps(input_payload.get('pythagoras_ext', {}), ensure_ascii=False) + "\n"
  )


def _format_messages_for_gemini(messages: list) -> str:
    """Gemini принимает простой текст. Склеиваем роли и контент в один промпт."""
    chunks = []
    for m in messages:
        role = m.get("role", "user")
        content = m.get("content", "")
        if not content:
            continue
        if role == "system":
            chunks.append(f"[system]\n{content}")
        elif role == "developer":
            chunks.append(f"[developer]\n{content}")
        else:
            chunks.append(f"[{role}]\n{content}")
    return "\n\n".join(chunks)

async def _openai_chat_completion(messages: list, *, temperature: float = 0.6, top_p: float = 0.9, max_tokens: int = 1400) -> dict:
    """Call OpenAI Chat Completions API with JSON-only response and model fallbacks."""
    if not OPENAI_API_KEY:
        raise RuntimeError("OPENAI_API_KEY is not set")
    url = "https://api.openai.com/v1/chat/completions"
    headers = {"Authorization": f"Bearer {OPENAI_API_KEY}", "Content-Type": "application/json"}

    # Prefer lightweight models that доступны на free-tier; при ошибке пробуем следующую
    model_candidates = [
        "gpt-5-mini",
        "gpt-4.1-mini",
    ]

    last_err_text = ""
    for model in model_candidates:
        payload = {
            "model": model,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "messages": messages,
            # Форсируем строгий JSON-ответ
            "response_format": {"type": "json_object"},
        }

        def _post():
            return requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)

        resp = await asyncio.to_thread(_post)
        if resp.status_code // 100 == 2:
            return resp.json()
        else:
            try:
                last_err_text = resp.text
            except Exception:
                last_err_text = f"HTTP {resp.status_code}"
            log.warning("OpenAI error on model %s: %s", model, last_err_text)
            # попробуем следующую модель

    raise RuntimeError(f"OpenAI all candidates failed. Last: {last_err_text}")


# --- Mistral Chat Completion ---
async def _mistral_chat_completion(messages: list, *, temperature: float = 0.6, top_p: float = 0.9, max_tokens: int = 1400) -> dict:
    """
    Call Mistral chat.completions API and normalize the response to OpenAI-like format.
    """
    if not MISTRAL_API_KEY:
        raise RuntimeError("MISTRAL_API_KEY is not set")

    url = "https://api.mistral.ai/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {MISTRAL_API_KEY}",
        "Content-Type": "application/json",
    }

    model_candidates = [
        "mistral-small-latest",
        "open-mixtral-8x7b",
    ]

    last_err_text = ""
    for model in model_candidates:
        payload = {
            "model": model,
            "temperature": temperature,
            "top_p": top_p,
            "max_tokens": max_tokens,
            "messages": messages,
            # Просим вернуть JSON; если модель не поддержит — всё равно попробуем распарсить
            "response_format": {"type": "json_object"},
        }

        def _post():
            return requests.post(url, headers=headers, data=json.dumps(payload), timeout=60)

        resp = await asyncio.to_thread(_post)
        if resp.status_code // 100 == 2:
            return resp.json()
        else:
            try:
                last_err_text = resp.text
            except Exception:
                last_err_text = f"HTTP {resp.status_code}"
            log.warning("Mistral error on model %s: %s", model, last_err_text)

    raise RuntimeError(f"Mistral all candidates failed. Last: {last_err_text}")


# Primary LLM router: OpenAI → Gemini → Mistral fallback
async def _llm_chat_completion(messages: list, *, temperature: float = 0.6, top_p: float = 0.9, max_tokens: int = 1400) -> dict:
    """Primary LLM router: OpenAI → Gemini → Mistral fallback. Возвращает объект в формате OpenAI ChatCompletions."""
    last_error = None

    # 1) Try OpenAI if key exists
    if OPENAI_API_KEY:
        try:
            return await _openai_chat_completion(messages, temperature=temperature, top_p=top_p, max_tokens=max_tokens)
        except Exception as e:
            last_error = e
            log.warning("OpenAI failed, will try Gemini fallback: %s", e)

    # 2) Try Gemini if key exists
    if GEMINI_API_KEY:
        try:
            url = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"
            headers = {"Content-Type": "application/json"}
            params = {"key": GEMINI_API_KEY}
            prompt_text = _format_messages_for_gemini(messages)
            payload = {
                "contents": [{"parts": [{"text": prompt_text}]}],
                "generationConfig": {"temperature": temperature, "topP": top_p, "maxOutputTokens": max_tokens},
            }
            def _post():
                return requests.post(url, headers=headers, params=params, data=json.dumps(payload), timeout=60)
            resp = await asyncio.to_thread(_post)
            if resp.status_code // 100 == 2:
                data = resp.json()
                text = ""
                try:
                    text = data.get("candidates", [{}])[0].get("content", {}).get("parts", [{}])[0].get("text", "")
                except Exception:
                    text = ""
                # Нормализуем под openai-формат для дальнейшего кода
                return {"choices": [{"message": {"content": text}}]}
            else:
                log.error("Gemini error %s: %s", resp.status_code, resp.text)
                raise RuntimeError(f"Gemini HTTP {resp.status_code}: {resp.text}")
        except Exception as e:
            last_error = e

    # 3) Try Mistral if key exists
    if MISTRAL_API_KEY:
        try:
            return await _mistral_chat_completion(messages, temperature=temperature, top_p=top_p, max_tokens=max_tokens)
        except Exception as e:
            last_error = e
            log.warning("Mistral failed as well: %s", e)

    # Если сюда дошли — нет доступных провайдеров или все упали
    if not OPENAI_API_KEY and not GEMINI_API_KEY and not MISTRAL_API_KEY:
        raise RuntimeError("No LLM keys configured (OPENAI_API_KEY / GEMINI_API_KEY / MISTRAL_API_KEY)")
    raise RuntimeError(f"All LLM providers failed (OpenAI/Gemini/Mistral). Last: {last_error}")

def _try_parse_json_from_text(text: str) -> dict:
    try:
        return json.loads(text)
    except Exception:
        # try to extract first JSON block
        start = text.find('{'); end = text.rfind('}')
        if start != -1 and end != -1 and end > start:
            snippet = text[start:end+1]
            try:
                return json.loads(snippet)
            except Exception:
                return {}
        return {}

def _render_report_markdown(report: dict) -> str:
    # Convert the returned JSON into a readable Telegram message
    parts = []
    title = report.get("title")
    if title:
        parts.append(f"*{title}*")
    summary = report.get("summary")
    if summary:
        parts.append(summary)
    lp = report.get("life_path", {})
    if lp:
        parts.append("")
        parts.append(f"*Число судьбы:* {lp.get('value')} — {lp.get('meaning','')}")
        if lp.get('strengths'):
            parts.append("_Сильные стороны:_ " + ", ".join(lp['strengths']))
        if lp.get('risks'):
            parts.append("_Риски:_ " + ", ".join(lp['risks']))
        if lp.get('advice'):
            parts.append("_Советы:_ " + ", ".join(lp['advice']))
    pm = report.get("pythagoras_matrix", {})
    if pm:
        grid = pm.get('grid_text')
        if grid:
            parts.append("")
            parts.append("*Матрица Пифагора*:\n````\n" + grid + "\n````")
        lo = pm.get('lines_overview') or []
        if lo:
            parts.append("")
            parts.append("*Линии и оси:*\n" + "\n".join([f"• {i.get('axis')}: {i.get('total')} — {i.get('tone')}. {i.get('comment','')}" for i in lo]))
    pr = report.get("practical_recs", {})
    if pr:
        if pr.get('week'):
            parts.append("")
            parts.append("*Рекомендации на неделю:*\n" + "\n".join(["• " + x for x in pr['week']]))
        if pr.get('month'):
            parts.append("")
            parts.append("*Рекомендации на месяц:*\n" + "\n".join(["• " + x for x in pr['month']]))
        if pr.get('focus_areas'):
            parts.append("")
            parts.append("*Фокусы:* " + ", ".join(pr['focus_areas']))
    dn = report.get("data_notes") or []
    if dn:
        parts.append("")
        parts.append("_Примечания к данным:_\n" + "\n".join(["• " + x for x in dn]))
    return "\n".join(parts) if parts else "Готово."

async def generate_and_send_numerology_report(update: Update, context: ContextTypes.DEFAULT_TYPE, *, full_name: str, dob: str, life_path: int, counts: dict, lines: dict, ext: dict, order_id: int | None):
    """Build prompt, call LLM, parse JSON, save to order meta, and send nicely formatted text."""
    input_payload = {
        "full_name": full_name,
        "dob_ddmmyyyy": dob,
        "life_path": life_path,
        "pythagoras_counts": counts,
        "pythagoras_lines": lines,
        "pythagoras_ext": ext,
    }
    if not OPENAI_API_KEY and not GEMINI_API_KEY:
        await update.message.reply_text("(Подробный отчёт временно недоступен: нет ключей LLM. Обратимся только к экспресс-разбору.)")
        return
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "system", "content": DEVELOPER_PROMPT},
        {"role": "user", "content": build_user_prompt_for_numerology(input_payload)},
    ]
    try:
        raw = await _llm_chat_completion(messages)
        content = (raw.get("choices") or [{}])[0].get("message", {}).get("content", "")
        report = _try_parse_json_from_text(content)
        if not report:
            await update.message.reply_text("Не удалось распарсить отчёт GPT. Попробуйте ещё раз позднее.")
            return
        # Save JSON to order meta
        if order_id:
            update_order(order_id, meta_merge={"llm_report": report})
        # Render and send
        md = _render_report_markdown(report)
        await update.message.reply_text(md, parse_mode="Markdown")
    except Exception as e:
        log.exception("LLM error: %s", e)
        # если пишет админ — покажем тех. причину
        try:
            is_admin = ADMIN_ID and update.effective_user and str(update.effective_user.id) == str(ADMIN_ID)
        except Exception:
            is_admin = False
        if is_admin:
            await update.message.reply_text(f"LLM error: {e}")
        else:
            await update.message.reply_text("Во время генерации отчёта произошла ошибка. Попробуем ещё раз чуть позже.")

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

# --- Нумерология: Матрица Пифагора (базовая по дате рождения) ---
def pythagoras_counts(date_str: str) -> dict:
    """Возвращает словарь {1..9: количество в дате рождения}. Нули не учитываются."""
    counts = {i: 0 for i in range(1, 10)}
    for ch in date_str:
        if ch.isdigit():
            d = int(ch)
            if d != 0:
                counts[d] += 1
    return counts

def render_pythagoras_grid(counts: dict) -> str:
    """Формирует 3x3 сетку 1-4-7 / 2-5-8 / 3-6-9.
    В каждой ячейке повторяем цифру столько раз, сколько встречается (или '—')."""
    def cell(n: int) -> str:
        c = counts.get(n, 0)
        return (str(n) * c) if c > 0 else "—"
    row1 = f"{cell(1):<7} | {cell(4):<7} | {cell(7):<7}"
    row2 = f"{cell(2):<7} | {cell(5):<7} | {cell(8):<7}"
    row3 = f"{cell(3):<7} | {cell(6):<7} | {cell(9):<7}"
    return "\n".join([row1, row2, row3])

# --- Матрица Пифагора: линии/столбцы/диагонали + короткие трактовки по насыщенности ---
def pythagoras_lines(counts: dict) -> dict:
    """Возвращает суммарные значения по классическим линиям матрицы.
    rows:  1-4-7 (character), 2-5-8 (energy), 3-6-9 (talent)
    cols:  1-2-3 (will/mind), 4-5-6 (responsibility/family), 7-8-9 (luck/spirit)
    diags: 1-5-9 (purpose), 3-5-7 (self-discipline)
    """
    get = lambda *nums: sum(counts.get(n, 0) for n in nums)
    return {
        "row_147": get(1,4,7),  # характер/воля
        "row_258": get(2,5,8),  # энергия/эмоции
        "row_369": get(3,6,9),  # талант/коммуникация
        "col_123": get(1,2,3),  # ум/целеустремленность
        "col_456": get(4,5,6),  # бытовая ответственность/семья
        "col_789": get(7,8,9),  # удача/духовная опора
        "diag_159": get(1,5,9), # предназначение/осевой вектор
        "diag_357": get(3,5,7), # самодисциплина/волевые привычки
    }

def _saturation_phrase(total: int) -> str:
    # Небольшая шкала насыщенности
    if total <= 0:
        return "пусто → зона для роста"
        
    if total == 1:
        return "тонкая линия → гибкий потенциал"

    if total == 2:
        return "сбалансировано → стабильная опора"

    if total == 3:
        return "выражено → заметная сила"

    return "перенасыщено → важно направлять экологично"

def render_pythagoras_summary(counts: dict) -> str:
    L = pythagoras_lines(counts)
    items = [
        ("1–4–7 (характер)",       L["row_147"]),
        ("2–5–8 (энергия)",        L["row_258"]),
        ("3–6–9 (талант)",         L["row_369"]),
        ("1–2–3 (ум/цель)",        L["col_123"]),
        ("4–5–6 (ответств.)",      L["col_456"]),
        ("7–8–9 (удача/дух.)",     L["col_789"]),
        ("1–5–9 (предназнач.)",    L["diag_159"]),
        ("3–5–7 (самодисп.)",      L["diag_357"]),
    ]
    # Сформируем компактные строки вида: «1–4–7 (характер): 2 — сбалансировано …»
    parts = []
    for name, total in items:
        parts.append(f"• {name}: {total} — {_saturation_phrase(total)}")
    return "\n".join(parts)

# --- Расширенная матрица Пифагора: интерпретации по цифрам 1..9 и сводка ---
DIGIT_MEANINGS = {
    1: {  # воля, лидерство, инициативность
        0: "нехватка инициативы; важно тренировать самостоятельность и личные решения",
        1: "искра воли и личного импульса; хватит на старт небольших дел",
        2: "стабильная воля и уверенность; хорошие лидерские зачатки",
        3: "сильный характер и напор; важно помнить об экологичности",
        4: "очень мощная воля; следи за тактом и гибкостью",
    },
    2: {  # эмоции, дипломатия, чувственность
        0: "эмоциональная сдержанность; развивать эмпатию и такт",
        1: "деликатность и чуткость к людям",
        2: "хорошая эмоциональная проводимость и дипломатия",
        3: "яркая эмоциональность; беречь границы",
        4: "сверхчувствительность; нужна гигиена эмоций",
    },
    3: {  # коммуникация, творчество
        0: "скромность в самовыражении; развивать голос и стиль",
        1: "творческая искра и чувство слова",
        2: "легкость общения и идей",
        3: "яркое самовыражение; уместны творческие проекты",
        4: "избыток говорения; полезно структурировать поток",
    },
    4: {  # дисциплина, структура, быт
        0: "слабая любовь к рутине; стоит вырастить систему",
        1: "базовая организованность",
        2: "надёжность и дисциплина",
        3: "сильная опора на порядок; не перегибать с контролем",
        4: "гиперконтроль; тренировать гибкость",
    },
    5: {  # энергия, здоровье, страсть к переменам
        0: "бережное отношение к ресурсам; важно накапливать силы",
        1: "живость и интерес к новому",
        2: "хороший тонус и любопытство",
        3: "высокая энергия; следить за режимом",
        4: "перегрев; выстраивать ритм и отдых",
    },
    6: {  # ответственность, семья, эстетика
        0: "фокус на собственных задачах; растить чувство дома",
        1: "забота о близких и вкус к красоте",
        2: "надёжность и семейность",
        3: "высокая ответственность; следить за балансом обязанностей",
        4: "риск жить только долгами/обязательствами — добавь радости",
    },
    7: {  # анализ, интроспекция, вера
        0: "нехватка пауз и анализа; добавь размышлений",
        1: "интерес к глубине и смыслам",
        2: "аналитичность и внутренняя опора",
        3: "сильная потребность уединяться; беречь баланс",
        4: "избыточная закрытость; полезны практики доверия",
    },
    8: {  # власть, управление, деньги
        0: "важно учиться обращаться с ресурсами",
        1: "базовые управленческие навыки",
        2: "хорошее чувство ресурса и влияния",
        3: "сильные амбиции; стоит беречь этику",
        4: "перенасыщение властью; держать экологичные рамки",
    },
    9: {  # гуманизм, завершение, служение
        0: "фокус на личном; стоит развивать сострадание",
        1: "чувство общего и эмпатия",
        2: "гуманизм и широта взглядов",
        3: "сильная миссионерская нота; беречь выгорание",
        4: "растворение в служении; помнить о себе",
    },
}

def digit_tier(count: int) -> int:
    """Сворачиваем все значения >=4 в один уровень интерпретации."""
    if count <= 0:
        return 0
    if count == 1:
        return 1
    if count == 2:
        return 2
    if count == 3:
        return 3
    return 4

def render_digit_interpretations(counts: dict) -> str:
    lines = []
    for d in range(1, 10):
        c = counts.get(d, 0)
        tier = digit_tier(c)
        meaning = DIGIT_MEANINGS.get(d, {}).get(tier, "")
        lines.append(f"{d}: {c} — {meaning}")
    return "\n".join(lines)

def extended_matrix_meta(counts: dict) -> dict:
    missing = [d for d in range(1,10) if counts.get(d,0) == 0]
    dominant = sorted([d for d in range(1,10) if counts.get(d,0) >= 3], key=lambda x: (-counts.get(x,0), x))
    return {
        "missing": missing,
        "dominant": dominant,
    }


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

        # Матрица Пифагора
        counts = pythagoras_counts(dob_str)
        grid_str = render_pythagoras_grid(counts)
        line_totals = pythagoras_lines(counts)
        lines_summary = render_pythagoras_summary(counts)
        ext = extended_matrix_meta(counts)
        digits_block = render_digit_interpretations(counts)

        order_id = ud.get("order_id")
        if order_id:
            update_order(order_id, status="done", meta_merge={
                "num_dob": dob_str,
                "num_name": full_name,
                "life_path": life_path,
                "pythagoras_counts": counts,
                "pythagoras_lines": line_totals,
                "pythagoras_ext": ext,
            })

        ud["flow"] = None; ud["state"] = None
        await update.message.reply_text(
            "**Нумерологический экспресс-разбор**\n\n"
            f"• Имя: *{full_name}*\n"
            f"• Дата рождения: *{dob_str}*\n"
            f"• Число судьбы: *{life_path}* — {meaning}\n\n"
            "Матрица Пифагора:\n"
            "```\n" + grid_str + "\n```\n\n"
            "Линии и оси матрицы:\n"
            + lines_summary + "\n\n"
            "Числа 1–9 (интерпретация по насыщенности):\n"
            "```\n" + digits_block + "\n```\n\n"
            "Отсутствующие числа: " + (", ".join(map(str, ext.get("missing", []))) or "—") + "\n"
            "Доминирующие числа: " + (", ".join(map(str, ext.get("dominant", []))) or "—") + "\n\n"
            "Это краткая версия. Полный разбор с дополнительными показателями и рекомендациями добавим в ближайшее время.",
            parse_mode="Markdown",
        )
        # Генерация подробного отчёта через GPT (параллельно после экспресс-вывода)
        try:
            await generate_and_send_numerology_report(
                update, context,
                full_name=full_name,
                dob=dob_str,
                life_path=life_path,
                counts=counts,
                lines=line_totals,
                ext=ext,
                order_id=order_id,
            )
        except Exception as e:
            log.exception("Failed to generate LLM report: %s", e)
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