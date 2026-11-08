# Деплой: Telegram-бот + мини-апп (Python, aiogram 3 + aiohttp)

Один процесс: бот (long polling) и веб-сервер мини-аппа на порту из переменной `PORT`.

- Старт: `python bot.py` (Procfile: `web: python bot.py`)
- Нужен публичный домен (Railway: Settings → Networking → Generate Domain)
- Переменные окружения:
  - `BOT_TOKEN` — токен от @BotFather (обязательно)
  - `WEBAPP_URL` — публичный адрес, например `https://xxx.up.railway.app` (без слэша в конце)
  - `ADMIN_IDS` — Telegram ID админов через запятую
  - `DB_PATH` — путь к SQLite, при подключённом Volume `/data/bot.db`
  - `DEV_MODE` — НЕ задавать на хостинге (только для локальной отладки)
- Нужен Volume в `/data`, иначе база сбрасывается при каждом деплое.
- Одна реплика: два процесса с одним токеном дают конфликт getUpdates.
