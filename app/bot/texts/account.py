from datetime import UTC, datetime
from html import escape
from math import ceil

from app.database.models import (
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
)
from app.services.traffic import TrafficFormatter

STATUS_TEXTS = {
    "active": "🟢 Активна",
    "trial": "🎁 Пробный период",
    "pending": "🟡 Готовится",
    "provisioning": "🟡 Готовится",
    "expired": "🔴 Закончилась",
    "disabled": "⚫ Отключена",
    "failed": "🟠 Требуется помощь",
    "activation_failed": "🟠 Требуется помощь",
}


def _value(value: object) -> str:
    return str(getattr(value, "value", value)).lower()


def get_subscription_status_text(status: object) -> str:
    return STATUS_TEXTS.get(_value(status), "🟠 Требуется помощь")


def _plural(number: int, forms: tuple[str, str, str]) -> str:
    if number % 10 == 1 and number % 100 != 11:
        form = forms[0]
    elif number % 10 in {2, 3, 4} and number % 100 not in {12, 13, 14}:
        form = forms[1]
    else:
        form = forms[2]
    return f"{number} {form}"


def format_time_left(expires_at: datetime, *, now: datetime | None = None) -> str:
    current = now or datetime.now(UTC)
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    seconds = (expires_at - current).total_seconds()
    if seconds <= 0:
        return "срок закончился"
    if seconds < 86400:
        return "меньше одного дня"
    total_days = ceil(seconds / 86400)
    months, days = divmod(total_days, 30)
    parts: list[str] = []
    if months:
        parts.append(_plural(months, ("месяц", "месяца", "месяцев")))
    if days:
        parts.append(_plural(days, ("день", "дня", "дней")))
    return " и ".join(parts)


def _number(value: float) -> str:
    rendered = f"{value:.1f}".rstrip("0").rstrip(".")
    return rendered.replace(".", ",")


def format_bytes(value: int) -> str:
    safe_value = max(0, value)
    if safe_value < 1024**3:
        return f"{_number(safe_value / 1024**2)} МБ"
    return f"{_number(safe_value / 1024**3)} ГБ"


def format_traffic(
    subscription: Subscription, *, sync_unavailable: bool = False
) -> str:
    remote_limit = getattr(
        subscription, "remnawave_traffic_limit_bytes", None
    )
    if hasattr(subscription, "remnawave_traffic_limit_bytes"):
        return TrafficFormatter.format(
            subscription.used_traffic_bytes,
            remote_limit,
            unlimited=(
                remote_limit == 0
                if remote_limit is not None
                else subscription.is_unlimited_traffic
            ),
            unavailable=sync_unavailable,
        )
    # Compatibility for old cached objects created before the traffic migration.
    if subscription.is_unlimited_traffic:
        return "Без ограничений"
    if subscription.used_traffic_bytes is None:
        return "Данные обновляются"
    used = format_bytes(subscription.used_traffic_bytes)
    if subscription.traffic_limit_gb is None:
        return used
    return f"{used} из {subscription.traffic_limit_gb} ГБ"


def format_devices(
    subscription: Subscription, *, sync_unavailable: bool = False
) -> str:
    connected = getattr(subscription, "connected_devices", None)
    limit = max(0, subscription.device_limit)
    if connected is None or sync_unavailable:
        return f"данные обновляются · лимит {limit}"
    connected = max(0, int(connected))
    return f"{connected} из {limit} подключено"


def get_account_state(
    subscription: Subscription,
    *,
    now: datetime | None = None,
) -> str:
    current = now or datetime.now(UTC)
    expires_at = subscription.expires_at
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=UTC)
    if current.tzinfo is None:
        current = current.replace(tzinfo=UTC)
    if expires_at <= current or subscription.status == SubscriptionStatus.expired:
        return "expired"
    if (
        subscription.status == SubscriptionStatus.activation_failed
        or subscription.provisioning_status == ProvisioningStatus.failed
    ):
        return "failed"
    if subscription.status == SubscriptionStatus.disabled:
        return "disabled"
    if (
        subscription.status == SubscriptionStatus.pending
        or subscription.provisioning_status
        in {
            ProvisioningStatus.not_started,
            ProvisioningStatus.pending,
            ProvisioningStatus.provisioning,
        }
    ):
        return "pending"
    if subscription.source_type == SubscriptionSource.trial:
        return "trial"
    return "active"


def _status_heading(state: str) -> str:
    return {
        "active": "🟢 <b>Активна</b>",
        "trial": "🎁 <b>Пробный период</b>",
        "pending": "🟡 <b>Доступ готовится</b>",
        "expired": "🔴 <b>Подписка закончилась</b>",
        "disabled": "⚫ <b>Подписка отключена</b>",
        "failed": "🟠 <b>Нужна помощь с доступом</b>",
    }[state]


def account_text(
    subscription: Subscription,
    tariff_name: str | None,
    *,
    now: datetime | None = None,
    sync_unavailable: bool = False,
) -> tuple[str, str]:
    state = get_account_state(subscription, now=now)
    time_left = format_time_left(subscription.expires_at, now=now)
    expiration = subscription.expires_at
    if expiration.tzinfo is None:
        expiration = expiration.replace(tzinfo=UTC)
    months = (
        "января",
        "февраля",
        "марта",
        "апреля",
        "мая",
        "июня",
        "июля",
        "августа",
        "сентября",
        "октября",
        "ноября",
        "декабря",
    )
    expiration_utc = expiration.astimezone(UTC)
    expiration_text = (
        f"{expiration_utc.day} {months[expiration_utc.month - 1]} "
        f"{expiration_utc.year}"
    )
    if tariff_name:
        display_tariff = tariff_name
    elif subscription.source_type == SubscriptionSource.paid:
        display_tariff = "Оплаченный тариф"
    elif subscription.source_type == SubscriptionSource.trial:
        display_tariff = "Пробный период"
    else:
        display_tariff = None
    heading = f"👤 <b>Моя подписка</b>\n\n{_status_heading(state)}"
    if state == "pending":
        return f"{heading}\n\nОбычно это занимает меньше минуты.", state
    if state == "failed":
        return (
            f"{heading}\n\nНе удалось обновить настройки. Напишите в поддержку — "
            "мы проверим доступ."
        ), state
    if state in {"expired", "disabled"}:
        date_label = "Закончилась" if state == "expired" else "Была активна до"
        return f"{heading}\n\n📅 {date_label}: <b>{expiration_text}</b>", state

    lines = [heading]
    if display_tariff is not None:
        lines.append(f"📦 Тариф: <b>{escape(display_tariff)}</b>")
    lines.extend(
        [
        f"📅 До <b>{expiration_text}</b> · осталось <b>{time_left}</b>",
        (
            "🌐 Трафик: "
            f"<b>{format_traffic(subscription, sync_unavailable=sync_unavailable)}</b>"
        ),
        (
            "📱 Устройства: "
            f"<b>{format_devices(subscription, sync_unavailable=sync_unavailable)}</b>"
        ),
        ]
    )
    if sync_unavailable:
        lines.append("⚠️ Показана последняя сохранённая информация.")
    text = "\n\n".join(lines)
    return text, state


def empty_account_text(*, trial_available: bool = False) -> str:
    action = (
        "Выберите тариф или попробуйте BlazeVPN бесплатно на 50 дней."
        if trial_available
        else "Выберите тариф, чтобы подключить BlazeVPN."
    )
    return f"👤 <b>Моя подписка</b>\n\nПодписки пока нет.\n\n{action}"
