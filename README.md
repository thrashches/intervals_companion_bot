<p align="center">
  <img src="docs/assets/bot-avatar.png" alt="Intervals Companion" width="160" height="160">
</p>

# Intervals Companion Bot

Telegram-бот-компаньон для [intervals.icu](https://intervals.icu): план тренировок, форма, анонсы и отчёты после активности.

Подробный план — в [plan.md](plan.md).

## Стек

- **bot/** — aiogram 3
- **backend/** — Django 5 + DRF + Admin
- **Celery + Beat** — синхронизация с intervals.icu и уведомления
- PostgreSQL, Redis, Docker Compose

## Быстрый старт

```bash
cp .env.example .env
# Заполните TELEGRAM_BOT_TOKEN (токен от @BotFather)

docker compose up --build
```

Сервисы:

| Сервис | URL / роль |
|--------|------------|
| backend | http://localhost:8000 |
| admin | http://localhost:8000/admin/ |
| bot | long polling |
| celery_worker / celery_beat | фоновые задачи |

Создать админа:

```bash
docker compose exec backend python manage.py createsuperuser
```

## Локальная разработка без Docker

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r backend/requirements.txt -r bot/requirements.txt
cp .env.example .env
# Для локального запуска можно использовать sqlite (по умолчанию, если нет DATABASE_URL)

cd backend && python manage.py migrate
celery -A config worker -l info &
celery -A config beat -l info &
python manage.py runserver

# в другом терминале
cd bot && BACKEND_URL=http://127.0.0.1:8000 python main.py
```

## Команды бота

- `/start` — подключение и статус
- `/plan` — сегодня / завтра / ближайшие события
- `/form` — CTL, ATL, Form, вес, VO2max
- `/settings` — время анонса, timezone, вкл/выкл
- `/disconnect` — удалить API-ключ
- `/help` — справка

## Тесты

```bash
cd backend
pytest
```

## Безопасность

- API-ключи intervals.icu хранятся зашифрованными (Fernet)
- Сообщение с ключом в Telegram удаляется после приёма
- Внутренний API бота защищён заголовком `X-Internal-Token`
