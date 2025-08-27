# Astro/Numero Telegram Bot — Starter (Railway-ready)

Этот репозиторий — каркас нашего бота с минимальным кодом:
- `/start` + меню-заглушки
- Подготовка к деплою на Railway
- Хранение секретов через `.env`

## ⚙️ Быстрый старт (локально)
1. Создай `.env` на основе `.env.example`.
2. Установи зависимости:
   ```bash
   python -m venv .venv && source .venv/bin/activate  # Windows: .venv\Scripts\activate
   pip install -r requirements.txt
   ```
3. Запусти бота:
   ```bash
   python -m src.bot
   ```

## 🚀 Деплой на Railway (шаги)
1. Создай репозиторий на GitHub (пустой или с README).
2. Склонируй себе локально и положи сюда файлы из этого архива.
3. Сделай коммит и пуш:
   ```bash
   git add .
   git commit -m "init: astro-num-bot starter"
   git push origin main
   ```
4. Зайди на Railway → New Project → Deploy from GitHub → выбери свой репозиторий.
5. В **Variables** добавь:
   - `BOT_TOKEN` — токен телеграм-бота
   - `OPENAI_API_KEY` — ключ OpenAI (можно временно пустым, если ещё не используешь)
6. В **Settings → Start Command** поставь:
   ```
   python -m src.bot
   ```
   (или оставь `Procfile`, Railway подхватит его автоматически)
7. Нажми Deploy. После запуска бот будет работать на long polling.

## 📁 Структура
```
.
├─ .env.example
├─ .gitignore
├─ Procfile
├─ requirements.txt
├─ README.md
└─ src
   ├─ __init__.py
   ├─ bot.py
   └─ config.py
```

## ✍️ Что дальше
- День 2: подключаем платежи (Telegram Stars).
- День 3–4: астрология (swisseph) и хиромантия (vision).
- День 5+: деплой, n8n-интеграции, PDF и пр.
