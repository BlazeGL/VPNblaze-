# VPN Telegram Bot — этап 3

Telegram-бот на aiogram 3 с PostgreSQL, Redis, SQLAlchemy 2, Alembic и отдельным
FastAPI-приложением для webhook. Реализованы тарифы и заказы предыдущих этапов,
trial на 7 дней, промокоды, локальные подписки, платежная модель и защищённая
админ-панель.

## Настройка

Скопируйте `.env.example` в `.env` и заполните как минимум:

- `TELEGRAM_BOT_TOKEN` — токен BotFather;
- `ADMIN_IDS` — Telegram ID администраторов через запятую;
- `POSTGRES_PASSWORD` — пароль PostgreSQL;
- `PUBLIC_BASE_URL` — публичный HTTPS-адрес сервиса webhook.

Переменные `ONLIPAY_*` оставлены предварительными. Публичная документация
merchant API OnliPay не опубликована, поэтому реальный transport и проверка
подписи намеренно заблокированы до получения официального контракта. Бот не
создаёт вымышленные endpoint'ы и не подтверждает оплату по success URL.

## Запуск

```bash
docker compose up -d --build
docker compose ps
```

Сервис `migrate` однократно применяет миграции. После его успешного завершения
запускаются `bot` и `webhook`. Endpoint webhook:

```text
POST /api/webhooks/onlipay
```

До подключения официального verifier endpoint отвечает `503` и не изменяет БД.

## Миграции

```bash
docker compose run --rm migrate alembic upgrade head
docker compose run --rm migrate alembic current
docker compose run --rm migrate alembic downgrade -1
```

## Проверки

```bash
.venv/Scripts/ruff check .
.venv/Scripts/pytest -p no:cacheprovider
```

## Команды бота

- `/start` — регистрация и главное меню;
- `/edik` — основная команда админ-панели, доступная только `ADMIN_IDS`;
- `/admin` — скрытый совместимый alias;
- `/new_promo` — пошаговое создание промокода администратором.

Trial и оплаченная подписка регистрируются локально через `SubscriptionService`.
До документированного подключения Remnawave бот не создаёт VPN-ссылки и честно
сообщает, что внешняя выдача доступа ожидает подключения сервиса.

## Логи

```bash
docker compose logs -f bot webhook
docker compose logs --tail=100 migrate postgres redis
```

## Remnawave (этап 4)

Бот использует официальный REST API Remnawave и создаёт отдельного пользователя
панели для каждого Telegram ID. Заполните `REMNAWAVE_API_TOKEN`,
`REMNAWAVE_INTERNAL_SQUAD_UUID` и `SUBSCRIPTION_ENCRYPTION_KEY` в `.env`.
Остальные параметры и безопасные значения по умолчанию приведены в
`.env.example`.

API token создаётся в панели Remnawave в разделе `API Tokens`. UUID Internal
Squad берётся из карточки нужного squad в разделе `Internal Squads`. Fernet-ключ
для шифрования ссылок можно создать командой:

```bash
python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
```

Администраторские команды: `/sync_remnawave`, `/grant_vpn`. Состояние интеграции
доступно в `/edik` → `🌐 Remnawave`. При временной недоступности API бот
продолжает работать, а оплаченные и trial-активации остаются в очереди повторов.
