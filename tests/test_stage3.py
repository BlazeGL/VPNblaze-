import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import UniqueConstraint

from app.bot.filters import AdminFilter
from app.bot.handlers.admin import open_edik, reject_edik
from app.bot.handlers.admin_promos import reject_new_promo
from app.database.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    PromoCode,
    PromoDiscountType,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    Tariff,
    TrialActivation,
    User,
)
from app.integrations.onlipay.client import OnliPayClient
from app.integrations.onlipay.exceptions import InvalidWebhookSignature
from app.integrations.onlipay.schemas import NormalizedPaymentStatus
from app.services.payments import PaymentService, PaymentValidationError
from app.services.promos import (
    PromoService,
    PromoValidationError,
    calculate_promo,
)
from app.services.subscriptions import (
    DeferredSubscriptionAdapter,
    SubscriptionService,
)
from app.services.trials import TrialService
from app.webhooks.onlipay import router as webhook_router


def session_mock() -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    session.get = AsyncMock()
    session.flush = AsyncMock()
    return session


def make_user(*, trial_used: bool = False) -> User:
    return User(
        id=1,
        telegram_id=1001,
        is_blocked=False,
        trial_used=trial_used,
        trial_disabled=False,
    )


def make_promo(
    discount_type: PromoDiscountType = PromoDiscountType.percent,
    value: str = "20",
    **values: object,
) -> PromoCode:
    defaults = {
        "id": uuid.uuid4(),
        "code": "VPN2026",
        "discount_type": discount_type,
        "discount_value": Decimal(value),
        "bonus_days": 7 if discount_type == PromoDiscountType.bonus_days else None,
        "max_uses": 100,
        "uses_count": 0,
        "per_user_limit": 1,
        "is_active": True,
        "created_by_admin_id": 1,
    }
    defaults.update(values)
    return PromoCode(**defaults)


@pytest.mark.asyncio
async def test_new_user_gets_exact_seven_day_trial() -> None:
    session = session_mock()
    user = make_user()
    session.scalar.side_effect = [user, None, None]
    subscription = Subscription(
        user_id=user.id,
        source_type=SubscriptionSource.trial,
        status=SubscriptionStatus.pending,
        started_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=7),
        device_limit=1,
    )
    subscription_service = MagicMock()
    subscription_service.register_trial = AsyncMock(return_value=subscription)
    now = datetime(2026, 7, 22, 10, 30, tzinfo=UTC)

    result = await TrialService(session, subscription_service).activate(
        user.telegram_id, now=now
    )

    assert result.activated is True
    assert result.activation is not None
    assert result.activation.expires_at - result.activation.started_at == timedelta(
        days=7
    )
    assert user.trial_used is True


@pytest.mark.asyncio
async def test_repeated_trial_is_rejected() -> None:
    session = session_mock()
    user = make_user(trial_used=True)
    session.scalar.side_effect = [user, None]

    result = await TrialService(session).activate(user.telegram_id)

    assert result.activated is False
    assert result.reason == "already_used"


def test_parallel_trial_has_database_unique_backstop() -> None:
    unique_column_sets = {
        tuple(constraint.columns.keys())
        for constraint in TrialActivation.__table__.constraints
        if isinstance(constraint, UniqueConstraint)
    }
    unique_column_sets.update(
        tuple(index.columns.keys())
        for index in TrialActivation.__table__.indexes
        if index.unique
    )
    assert ("user_id",) in unique_column_sets


@pytest.mark.asyncio
async def test_non_admin_has_no_edik_access() -> None:
    event = SimpleNamespace(from_user=SimpleNamespace(id=7))
    assert await AdminFilter()(event, {42}) is False
    message = SimpleNamespace(answer=AsyncMock())
    await reject_edik(message)
    message.answer.assert_awaited_once_with(
        "⛔ У вас нет доступа к панели управления."
    )


@pytest.mark.asyncio
async def test_admin_has_edik_access() -> None:
    event = SimpleNamespace(from_user=SimpleNamespace(id=42))
    assert await AdminFilter()(event, {42}) is True
    message = SimpleNamespace(answer=AsyncMock())
    await open_edik(message)
    assert message.answer.await_args.args[0] == "Панель администратора"


@pytest.mark.asyncio
async def test_non_admin_cannot_run_new_promo() -> None:
    event = SimpleNamespace(from_user=SimpleNamespace(id=7))
    assert await AdminFilter()(event, {42}) is False
    message = SimpleNamespace(answer=AsyncMock())
    await reject_new_promo(message)
    message.answer.assert_awaited_once_with(
        "⛔ У вас нет доступа к панели управления."
    )


async def create_promo_of_type(discount_type: PromoDiscountType) -> PromoCode:
    session = session_mock()
    service = PromoService(session)
    service.get_by_code = AsyncMock(return_value=None)  # type: ignore[method-assign]
    value = (
        Decimal("7")
        if discount_type == PromoDiscountType.bonus_days
        else Decimal("20")
    )
    return await service.create(
        code=" vpn2026 ",
        discount_type=discount_type,
        discount_value=value,
        bonus_days=7 if discount_type == PromoDiscountType.bonus_days else None,
        max_uses=100,
        per_user_limit=1,
        minimum_order_amount=None,
        valid_from=None,
        valid_until=None,
        created_by_admin_id=1,
        tariff_ids=None,
        actor_telegram_id=42,
    )


@pytest.mark.asyncio
async def test_create_percent_promo() -> None:
    promo = await create_promo_of_type(PromoDiscountType.percent)
    assert promo.code == "VPN2026"
    assert promo.discount_type == PromoDiscountType.percent


@pytest.mark.asyncio
async def test_create_fixed_promo() -> None:
    promo = await create_promo_of_type(PromoDiscountType.fixed)
    assert promo.discount_type == PromoDiscountType.fixed


@pytest.mark.asyncio
async def test_create_bonus_days_promo() -> None:
    promo = await create_promo_of_type(PromoDiscountType.bonus_days)
    assert promo.bonus_days == 7


@pytest.mark.asyncio
async def test_expired_promo_is_rejected() -> None:
    session = session_mock()
    promo = make_promo(valid_until=datetime.now(UTC) - timedelta(seconds=1))
    with pytest.raises(PromoValidationError, match="expired"):
        await PromoService(session).validate(
            promo, user_id=1, tariff_id=1, amount=Decimal("499")
        )


@pytest.mark.asyncio
async def test_max_uses_is_enforced() -> None:
    session = session_mock()
    promo = make_promo(max_uses=1, uses_count=1)
    with pytest.raises(PromoValidationError, match="max_uses_reached"):
        await PromoService(session).validate(
            promo, user_id=1, tariff_id=1, amount=Decimal("499")
        )


@pytest.mark.asyncio
async def test_per_user_limit_is_enforced() -> None:
    session = session_mock()
    session.scalar.side_effect = [0, 1]
    promo = make_promo(per_user_limit=1)
    with pytest.raises(PromoValidationError, match="per_user_limit_reached"):
        await PromoService(session).validate(
            promo, user_id=1, tariff_id=1, amount=Decimal("499")
        )


def test_discount_never_makes_price_negative() -> None:
    promo = make_promo(PromoDiscountType.fixed, "1000")
    application = calculate_promo(promo, Decimal("100"))
    assert application.discount_amount == Decimal("100.00")
    assert application.final_amount == Decimal("0.00")


@pytest.mark.asyncio
async def test_promo_snapshot_is_saved_on_order() -> None:
    session = session_mock()
    promo = make_promo()
    order = Order(
        id=uuid.uuid4(),
        user_id=1,
        tariff_id=2,
        status=OrderStatus.pending,
        amount_snapshot=Decimal("499"),
        original_amount=Decimal("499"),
        final_amount=Decimal("499"),
    )
    service = PromoService(session)
    service.get_by_code = AsyncMock(return_value=promo)  # type: ignore[method-assign]
    service.validate = AsyncMock(  # type: ignore[method-assign]
        return_value=calculate_promo(promo, Decimal("499"))
    )

    await service.apply_to_order(order, user_id=1, code="vpn2026")

    assert order.promo_snapshot_code == "VPN2026"
    assert order.promo_snapshot_type == "percent"
    assert order.final_amount == Decimal("399.20")


def make_payment_and_order() -> tuple[Payment, Order]:
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        user_id=1,
        tariff_id=1,
        status=OrderStatus.awaiting_payment,
        tariff_name_snapshot="30 days",
        duration_days_snapshot=30,
        traffic_limit_gb_snapshot=100,
        is_unlimited_traffic_snapshot=False,
        device_limit_snapshot=3,
        amount_snapshot=Decimal("499"),
        original_amount=Decimal("499"),
        discount_amount=Decimal("0"),
        final_amount=Decimal("499"),
        bonus_days=0,
        currency_snapshot="RUB",
    )
    payment = Payment(
        id=uuid.uuid4(),
        order_id=order_id,
        provider_payment_id="provider-1",
        status=PaymentStatus.pending,
        amount=Decimal("499"),
        currency="RUB",
        payment_url="https://merchant.invalid/payment-id-from-fake-test",
        idempotency_key="idem-1",
    )
    return payment, order


@pytest.mark.asyncio
async def test_repeated_webhook_does_not_extend_subscription() -> None:
    session = session_mock()
    payment, order = make_payment_and_order()
    order.status = OrderStatus.completed
    session.scalar.side_effect = [payment, order]
    subscriptions = MagicMock()
    subscriptions.extend_from_paid_order = AsyncMock()

    result = await PaymentService(
        session, OnliPayClient(), subscription_service=subscriptions
    ).process_confirmed_payment(
        provider_payment_id=payment.provider_payment_id,
        reported_order_id=str(order.id),
        amount=payment.amount,
        currency="RUB",
    )

    assert result.already_processed is True
    subscriptions.extend_from_paid_order.assert_not_awaited()


def test_invalid_webhook_signature_does_not_open_db_session() -> None:
    class InvalidVerifier:
        def verify_and_decode(self, body: bytes, headers: object) -> object:
            raise InvalidWebhookSignature

    class SessionFactory:
        calls = 0

        def __call__(self) -> object:
            self.calls += 1
            raise AssertionError("DB must not be opened")

    app = FastAPI()
    app.include_router(webhook_router)
    factory = SessionFactory()
    app.state.settings = SimpleNamespace(onlipay_merchant_id="merchant")
    app.state.onlipay_webhook_verifier = InvalidVerifier()
    app.state.session_factory = factory
    app.state.onlipay_client = OnliPayClient()

    response = TestClient(app).post("/api/webhooks/onlipay", content=b"invalid")

    assert response.status_code == 401
    assert factory.calls == 0


@pytest.mark.asyncio
async def test_wrong_amount_does_not_activate_order() -> None:
    session = session_mock()
    payment, order = make_payment_and_order()
    session.scalar.side_effect = [payment, order]
    with pytest.raises(PaymentValidationError, match="amount_mismatch"):
        await PaymentService(session, OnliPayClient()).process_confirmed_payment(
            provider_payment_id=payment.provider_payment_id,
            reported_order_id=str(order.id),
            amount=Decimal("1"),
            currency="RUB",
        )
    assert order.status == OrderStatus.awaiting_payment


@pytest.mark.asyncio
async def test_successful_payment_completes_order() -> None:
    session = session_mock()
    payment, order = make_payment_and_order()
    user = make_user()
    session.scalar.side_effect = [payment, order]
    session.get.return_value = user
    subscription = Subscription(
        user_id=1,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.pending,
        started_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
        device_limit=3,
    )
    subscriptions = MagicMock()
    subscriptions.extend_from_paid_order = AsyncMock(return_value=subscription)

    result = await PaymentService(
        session, OnliPayClient(), subscription_service=subscriptions
    ).process_confirmed_payment(
        provider_payment_id=payment.provider_payment_id,
        reported_order_id=str(order.id),
        amount=payment.amount,
        currency="RUB",
    )

    assert result.completed is True
    assert order.status == OrderStatus.completed
    assert payment.status == PaymentStatus.paid


@pytest.mark.asyncio
async def test_bonus_days_are_added_to_subscription() -> None:
    session = session_mock()
    session.scalar.return_value = None
    payment, order = make_payment_and_order()
    order.duration_days_snapshot = 30
    order.bonus_days = 7
    now = datetime(2026, 7, 22, tzinfo=UTC)

    subscription = await SubscriptionService(
        session, DeferredSubscriptionAdapter()
    ).extend_from_paid_order(make_user(), order, now=now)

    assert subscription.expires_at == now + timedelta(days=37)


@pytest.mark.asyncio
async def test_activation_retry_does_not_add_paid_days_twice() -> None:
    session = session_mock()
    payment, order = make_payment_and_order()
    original_expiry = datetime(2026, 9, 1, tzinfo=UTC)
    subscription = Subscription(
        user_id=1,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.activation_failed,
        started_at=datetime(2026, 7, 1, tzinfo=UTC),
        expires_at=original_expiry,
        order_id=order.id,
        device_limit=3,
    )
    session.scalar.return_value = subscription

    retried = await SubscriptionService(
        session, DeferredSubscriptionAdapter()
    ).extend_from_paid_order(make_user(), order)

    assert retried.expires_at == original_expiry


@pytest.mark.asyncio
async def test_tariff_change_does_not_change_order_price_snapshot() -> None:
    session = session_mock()
    tariff = Tariff(
        id=1,
        name="30 days",
        duration_days=30,
        price=Decimal("499"),
        currency="RUB",
        traffic_limit_gb=100,
        is_unlimited_traffic=False,
        device_limit=3,
    )
    from app.database.repositories import OrderRepository

    order = await OrderRepository(session).create_from_tariff(1, tariff)
    tariff.price = Decimal("999")
    assert order.original_amount == Decimal("499")
    assert order.final_amount == Decimal("499")


def test_provider_statuses_are_internal_not_raw_onlipay_guesses() -> None:
    assert NormalizedPaymentStatus.paid.value == "paid"
