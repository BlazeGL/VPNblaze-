# VPN Telegram Bot: этап 1

Минимальный запускаемый каркас Telegram-бота на aiogram 3 с PostgreSQL,
Redis, SQLAlchemy 2 и Alembic. Бот работает через long polling.

## Настройка

Создайте `.env` на основе `.env.example` и заполните:

- `TELEGRAM_BOT_TOKEN` — токен от BotFather;
- `ADMIN_IDS` — Telegram ID администраторов через запятую, либо пустая строка;
- `POSTGRES_PASSWORD` — стойкий пароль PostgreSQL.

Остальные значения подходят для запуска через Docker Compose без изменений.

## Запуск

```bash
cp .env.example .env
docker compose up -d --build
docker compose ps
```

При запуске контейнер `bot` ждёт healthcheck PostgreSQL и Redis, применяет
миграции, затем запускает long polling.

## Миграции

```bash
docker compose run --rm bot alembic upgrade head
docker compose run --rm bot alembic current
docker compose run --rm bot alembic downgrade -1
```

Создание следующей миграции после изменения моделей:

```bash
docker compose run --rm bot alembic revision --autogenerate -m "description"
```

## Проверки

Локально с Python 3.12:

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"
.venv/Scripts/ruff check .
.venv/Scripts/pytest
```

В Linux/macOS вместо `.venv/Scripts/` используется `.venv/bin/`.

## Логи и остановка

```bash
docker compose logs -f bot
docker compose logs --tail=100 postgres redis
docker compose down
```

Команда `/start` создаёт пользователя или обновляет его Telegram-данные,
после чего показывает приветствие и inline-меню. Все пункты меню на этом этапе
отвечают: «Раздел находится в разработке».
