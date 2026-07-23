from datetime import UTC, datetime
from html import escape
from math import ceil

from app.database.models import (
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
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
        "active": "🟢 <b>VPN работает</b>",
        "trial": "🎁 <b>Пробный период активен</b>",
        "pending": "🟡 <b>Доступ готовится</b>",
        "expired": "🔴 <b>Подписка закончилась</b>",
        "disabled": "⚫ <b>Подписка отключена</b>",
        "failed": "🟠 <b>Нужна помощь с доступом</b>",
    }[state]


def _helpful_message(
    state: str,
    *,
    time_left: str,
    sync_unavailable: bool,
) -> str:
    if sync_unavailable:
        return (
            "⚠️ Сейчас не удалось получить свежие данные с сервера. Ниже показана "
            "последняя сохранённая информация."
        )
    if state == "trial":
        return "🎁 Сейчас у вас бесплатный пробный период."
    if state == "expired":
        return (
            "🔴 Срок подписки закончился. После продления ваш прежний ключ снова "
            "заработает."
        )
    if state == "pending":
        return (
            "⏳ Мы готовим ваш доступ. Обычно это занимает меньше минуты."
        )
    if state == "failed":
        return (
            "⚠️ Не удалось обновить данные. Ваш платёж сохранён. Обратитесь в "
            "поддержку или попробуйте обновить информацию позже."
        )
    if state == "disabled":
        return (
            "⚫ Доступ отключён. Пополните баланс минимум на 5 ₽ и "
            "активируйте VPN снова."
        )
    if time_left in {"меньше одного дня", "1 день", "2 дня", "3 дня"}:
        return (
            "⚠️ Подписка скоро закончится. Продлите её, чтобы VPN не отключился."
        )
    return "✅ Всё готово. Можно пользоваться VPN."


def account_text(
    subscription: Subscription,
    tariff_name: str | None,
    *,
    user: User | None = None,
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
    elif subscription.source_type == SubscriptionSource.trial:
        display_tariff = "Пробный период"
    else:
        display_tariff = "Индивидуальный"
    helpful_message = _helpful_message(
        state,
        time_left=time_left,
        sync_unavailable=sync_unavailable,
    )
    financial_summary = ""
    if user is not None:
        financial_summary = (
            "💰 Баланс:\n"
            f"<b>{_number(float(user.balance))} ₽</b>\n\n"
            "👥 Приглашено друзей:\n"
            f"<b>{user.total_referrals}</b>\n\n"
            "🎁 Заработано:\n"
            f"<b>{_number(float(user.total_referral_income))} ₽</b>\n\n"
        )
    text = (
        "👤 <b>Личный кабинет</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{_status_heading(state)}\n\n"
        f"{financial_summary}"
        "📅 Доступ до:\n"
        f"<b>{expiration_text}</b>\n\n"
        "⏳ Осталось:\n"
        f"<b>{time_left}</b>\n\n"
        "📦 Тариф:\n"
        f"<b>{escape(display_tariff)}</b>\n\n"
        "🌐 Трафик:\n"
        f"<b>{format_traffic(subscription, sync_unavailable=sync_unavailable)}</b>\n\n"
        "📱 Устройств:\n"
        f"<b>до {subscription.device_limit}</b>\n\n"
        "━━━━━━━━━━━━━━━━━━\n\n"
        f"{helpful_message}"
    )
    return text, state


def empty_account_text(user: User | None = None) -> str:
    financial_summary = ""
    if user is not None:
        financial_summary = (
            "💰 Баланс:\n"
            f"<b>{_number(float(user.balance))} ₽</b>\n\n"
            "👥 Приглашено друзей:\n"
            f"<b>{user.total_referrals}</b>\n\n"
            "🎁 Заработано:\n"
            f"<b>{_number(float(user.total_referral_income))} ₽</b>\n\n"
        )
    return (
        "👤 <b>Личный кабинет</b>\n\n"
        f"{financial_summary}"
        "У вас пока нет подписки.\n\n"
        "Выберите тариф или попробуйте BlazeVPN бесплатно."
    )
