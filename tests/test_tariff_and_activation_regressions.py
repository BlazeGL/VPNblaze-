import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.handlers import tariffs as tariff_handlers
from app.bot.handlers import user_commands
from app.bot.handlers.tariffs import buy_tariff, render_tariff_screen
from app.bot.keyboards.tariffs import build_order
from app.database.models import (
    ProvisioningOperation,
    ProvisioningOperationStatus,
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    Tariff,
    User,
)
from app.services.activation_notifications import (
    deliver_activation_notification,
)
from app.services.remnawave import RemnawaveProvisioningService


def canonical_tariff() -> Tariff:
    return Tariff(
        id=1,
        name="BlazeVPN — 30 дней",
        duration_days=30,
        price=Decimal("99.00"),
        currency="RUB",
        traffic_limit_gb=600,
        is_unlimited_traffic=False,
        device_limit=3,
        is_active=True,
    )


def active_subscription() -> Subscription:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    return Subscription(
        id=uuid.uuid4(),
        user_id=1,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.active,
        provisioning_status=ProvisioningStatus.active,
        started_at=now,
        expires_at=now + timedelta(days=30),
        traffic_limit_gb=600,
        is_unlimited_traffic=False,
        device_limit=3,
    )


def notification_session(
    subscription: Subscription,
) -> tuple[MagicMock, User]:
    session = MagicMock()
    user = User(id=1, telegram_id=777)
    session.scalar = AsyncMock(return_value=subscription)
    session.get = AsyncMock(return_value=user)
    session.flush = AsyncMock()
    return session, user


def test_tariff_screen_contains_only_current_price_and_limits() -> None:
    text, markup = render_tariff_screen(canonical_tariff())
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert text == (
        "⚡ <b>BlazeVPN — 30 дней</b>\n\n"
        "💳 Стоимость: <b>99 ₽</b>\n"
        "📅 Срок: <b>30 дней</b>\n"
        "🌐 Трафик: <b>600 ГБ</b>"
    )
    assert "199" not in text
    assert [button.text for button in buttons] == [
        "💳 Купить за 99 ₽",
        "⬅️ Назад",
    ]


def test_tariff_screen_escapes_admin_entered_html() -> None:
    item = canonical_tariff()
    item.name = "<b>& небезопасное имя"

    text, _ = render_tariff_screen(item)

    assert "&lt;b&gt;&amp; небезопасное имя" in text
    assert "<b>& небезопасное имя" not in text


def test_each_active_tariff_is_available_from_catalog_card() -> None:
    monthly = canonical_tariff()
    quarterly = Tariff(
        id=2,
        name="BlazeVPN — 90 дней",
        duration_days=90,
        price=Decimal("249.50"),
        currency="RUB",
        traffic_limit_gb=600,
        is_unlimited_traffic=False,
        device_limit=5,
        is_active=True,
    )

    _, markup = render_tariff_screen(monthly, [monthly, quarterly])
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert [button.text for button in buttons[:2]] == [
        "✅ BlazeVPN — 30 дней · 99 ₽",
        "BlazeVPN — 90 дней · 249.5 ₽",
    ]
    assert all(button.callback_data for button in buttons[:2])


def test_catalog_can_hide_actual_price_and_keep_custom_monthly_text() -> None:
    monthly = canonical_tariff()
    quarterly = Tariff(
        id=2,
        name="🔥 Пополнить на 3 месяца — 139 ₽/мес",
        duration_days=90,
        price=Decimal("417.00"),
        show_price_in_button=False,
        currency="RUB",
        traffic_limit_gb=600,
        is_unlimited_traffic=False,
        device_limit=5,
        is_active=True,
    )

    _, markup = render_tariff_screen(monthly, [monthly, quarterly])
    buttons = [button for row in markup.inline_keyboard for button in row]

    assert buttons[1].text == "🔥 Пополнить на 3 месяца — 139 ₽/мес"
    assert "417" not in buttons[1].text


def test_order_menu_uses_discounted_price_without_promo_button() -> None:
    order = SimpleNamespace(
        id=uuid.uuid4(),
        original_amount=Decimal("99.00"),
        final_amount=Decimal("79.00"),
    )

    buttons = [
        button for row in build_order(order).inline_keyboard for button in row
    ]

    assert buttons[0].text == "💰 Купить с баланса за 79 ₽"
    assert all("промокод" not in button.text.lower() for button in buttons)


@pytest.mark.asyncio
async def test_saved_plain_promo_is_applied_when_tariff_is_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = MagicMock()
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message = MagicMock()
    callback_data = SimpleNamespace(tariff_id=1)
    state = MagicMock()
    state.get_data = AsyncMock(return_value={"pending_promo_code": "VPN2026"})
    state.set_data = AsyncMock()
    user = SimpleNamespace(id=7, balance=Decimal("150.00"))
    tariff = canonical_tariff()
    order = SimpleNamespace(
        id=uuid.uuid4(),
        user_id=user.id,
        tariff_name_snapshot=tariff.name,
        duration_days_snapshot=tariff.duration_days,
        traffic_limit_gb_snapshot=tariff.traffic_limit_gb,
        is_unlimited_traffic_snapshot=False,
        device_limit_snapshot=tariff.device_limit,
        currency_snapshot="RUB",
        original_amount=Decimal("99.00"),
        final_amount=Decimal("99.00"),
    )
    user_repository = MagicMock()
    user_repository.get_by_telegram_id = AsyncMock(return_value=user)
    tariff_repository = MagicMock()
    tariff_repository.get_by_id = AsyncMock(return_value=tariff)
    order_repository = MagicMock()
    order_repository.create_from_tariff = AsyncMock(return_value=order)
    promo_service = MagicMock()

    async def apply_promo(*args: object, **kwargs: object) -> SimpleNamespace:
        order.final_amount = Decimal("79.00")
        return SimpleNamespace()

    promo_service.apply_to_order = AsyncMock(side_effect=apply_promo)
    monkeypatch.setattr(
        tariff_handlers, "UserRepository", MagicMock(return_value=user_repository)
    )
    monkeypatch.setattr(
        tariff_handlers, "TariffRepository", MagicMock(return_value=tariff_repository)
    )
    monkeypatch.setattr(
        tariff_handlers, "OrderRepository", MagicMock(return_value=order_repository)
    )
    monkeypatch.setattr(
        tariff_handlers, "PromoService", MagicMock(return_value=promo_service)
    )
    renderer = AsyncMock()
    monkeypatch.setattr(tariff_handlers, "edit_text_or_caption", renderer)
    session = MagicMock()
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = transaction
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=context)

    await buy_tariff(callback, callback_data, session_factory, state)

    promo_service.apply_to_order.assert_awaited_once_with(
        order,
        user_id=user.id,
        code="VPN2026",
        actor_telegram_id=123,
    )
    state.set_data.assert_awaited_once_with({})
    rendered_text = renderer.await_args.args[1]
    assert "Промокод применён автоматически" in rendered_text
    assert "К оплате: 79 ₽" in rendered_text


@pytest.mark.asyncio
async def test_plans_command_reuses_tariffs_callback_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    renderer = AsyncMock()
    monkeypatch.setattr(user_commands, "show_tariffs", renderer)
    message = MagicMock()
    session_factory = MagicMock()

    await user_commands.plans_command(message, session_factory)

    renderer.assert_awaited_once()
    callback = renderer.await_args.args[0]
    assert callback.data == "tariffs"


@pytest.mark.asyncio
async def test_ten_activation_checks_send_exactly_one_notification() -> None:
    subscription = active_subscription()
    session, _ = notification_session(subscription)
    sender = AsyncMock()

    results = [
        await deliver_activation_notification(
            session,
            subscription_id=subscription.id,
            cipher=None,
            sender=sender,
        )
        for _ in range(10)
    ]

    assert results == [True] + [False] * 9
    sender.assert_awaited_once()
    assert subscription.activation_notified_at is not None


@pytest.mark.asyncio
async def test_telegram_failure_leaves_activation_notification_retryable() -> None:
    subscription = active_subscription()
    session, _ = notification_session(subscription)
    sender = AsyncMock(side_effect=RuntimeError("telegram unavailable"))

    with pytest.raises(RuntimeError, match="telegram unavailable"):
        await deliver_activation_notification(
            session,
            subscription_id=subscription.id,
            cipher=None,
            sender=sender,
        )

    assert subscription.activation_notified_at is None
    sender.side_effect = None
    assert await deliver_activation_notification(
        session,
        subscription_id=subscription.id,
        cipher=None,
        sender=sender,
    )
    assert subscription.activation_notified_at is not None


@pytest.mark.asyncio
async def test_completed_provisioning_repairs_stale_pending_subscription() -> None:
    subscription = active_subscription()
    subscription.status = SubscriptionStatus.pending
    subscription.provisioning_status = ProvisioningStatus.pending
    user = User(id=1, telegram_id=777)
    operation = ProvisioningOperation(
        user_id=user.id,
        subscription_id=subscription.id,
        idempotency_key=f"paid:{subscription.id}",
        source=SubscriptionSource.paid.value,
        status=ProvisioningOperationStatus.completed,
        attempts=1,
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=operation)
    session.flush = AsyncMock()

    result = await RemnawaveProvisioningService(
        session,
        MagicMock(),
        MagicMock(),
        "aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa",
        "cccccccc-cccc-4ccc-8ccc-cccccccccccc",
    ).provision(subscription, user)

    assert result.status == SubscriptionStatus.active
    assert subscription.status == SubscriptionStatus.active
    assert subscription.provisioning_status == ProvisioningStatus.active
