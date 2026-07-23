import uuid
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)
from app.integrations.onlipay.client import OnliPayClient
from app.services.balance import BalanceService
from app.services.billing import BillingService
from app.services.payments import PaymentService
from app.services.referrals import ReferralService
from app.services.traffic import TrafficFormatter


def session_with_scalars(*values: object) -> MagicMock:
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=values)
    session.flush = AsyncMock()
    return session


def user(user_id: int, *, balance: str = "0.00") -> User:
    return User(
        id=user_id,
        telegram_id=1000 + user_id,
        balance=Decimal(balance),
        referral_code=f"code-{user_id}",
        total_referrals=0,
        total_referral_income=Decimal("0.00"),
    )


@pytest.mark.asyncio
async def test_referral_bonus_is_awarded_once() -> None:
    invitee = user(2)
    referrer = user(1)
    session = session_with_scalars(
        invitee,
        referrer,
        referrer,
        None,
        invitee,
    )
    service = ReferralService(session)

    first = await service.award_registration_bonus(
        invitee, f"REF_{referrer.referral_code}"
    )
    second = await service.award_registration_bonus(
        invitee, f"REF_{referrer.referral_code}"
    )

    assert first.awarded is True
    assert second.awarded is False
    assert second.reason == "already_referred"
    assert invitee.referred_by == referrer.id
    assert referrer.balance == Decimal("50.00")
    assert referrer.total_referrals == 1
    assert referrer.total_referral_income == Decimal("50.00")
    transaction = session.add.call_args.args[0]
    assert transaction.type == BalanceTransactionType.referral_bonus


@pytest.mark.asyncio
async def test_self_referral_is_rejected() -> None:
    invitee = user(1)
    session = session_with_scalars(invitee, invitee)

    result = await ReferralService(session).award_registration_bonus(
        invitee, f"REF_{invitee.referral_code}"
    )

    assert result.awarded is False
    assert result.reason == "self_referral"
    session.add.assert_not_called()


def paid_subscription(user_id: int = 1) -> Subscription:
    return Subscription(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.active,
        provisioning_status=ProvisioningStatus.active,
        started_at=SimpleNamespace(),
        expires_at=SimpleNamespace(),
        device_limit=1,
    )


@pytest.mark.asyncio
async def test_daily_billing_charges_five_rubles_once() -> None:
    owner = user(1, balance="10.00")
    subscription = paid_subscription()
    day = date(2026, 7, 23)
    session = session_with_scalars(subscription, owner, subscription, None)

    result = await BillingService(session).charge_subscription(
        subscription.id, billing_date=day
    )

    assert result.charged is True
    assert owner.balance == Decimal("5.00")
    transaction = session.add.call_args.args[0]
    assert transaction.amount == Decimal("-5.00")
    assert transaction.idempotency_key.endswith(day.isoformat())
    assert transaction.balance_before == Decimal("10.00")
    assert transaction.balance_after == Decimal("5.00")


@pytest.mark.asyncio
async def test_daily_billing_is_idempotent() -> None:
    owner = user(1, balance="10.00")
    subscription = paid_subscription()
    transaction = BalanceTransaction(
        user_id=owner.id,
        type=BalanceTransactionType.daily_charge,
        amount=Decimal("-5.00"),
        balance_before=Decimal("10.00"),
        balance_after=Decimal("5.00"),
        idempotency_key=f"daily:{subscription.id}:2026-07-23",
    )
    owner.balance = Decimal("5.00")
    session = session_with_scalars(
        subscription, owner, subscription, transaction
    )

    result = await BillingService(session).charge_subscription(
        subscription.id, billing_date=date(2026, 7, 23)
    )

    assert result.already_processed is True
    assert owner.balance == Decimal("5.00")
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_insufficient_balance_disables_subscription() -> None:
    owner = user(1, balance="4.99")
    subscription = paid_subscription()
    session = session_with_scalars(subscription, owner, subscription, None)

    result = await BillingService(session).charge_subscription(
        subscription.id, billing_date=date(2026, 7, 23)
    )

    assert result.disabled is True
    assert owner.balance == Decimal("4.99")
    assert subscription.status == SubscriptionStatus.disabled
    assert subscription.provisioning_status == ProvisioningStatus.disabled
    session.add.assert_not_called()


@pytest.mark.asyncio
async def test_balance_history_records_before_and_after() -> None:
    owner = user(1, balance="100.00")
    session = session_with_scalars(owner, None)

    change = await BalanceService(session).credit(
        owner.id,
        amount="25",
        transaction_type=BalanceTransactionType.topup,
        idempotency_key="payment:example",
        reference_type="payment",
        reference_id="example",
    )

    assert change.transaction.balance_before == Decimal("100.00")
    assert change.transaction.balance_after == Decimal("125.00")
    assert owner.balance == Decimal("125.00")


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (512, "0.5 КБ"),
        (156 * 1024**2, "156 МБ"),
        (int(2.4 * 1024**3), "2.4 ГБ"),
        (2 * 1024**4, "2 ТБ"),
    ],
)
def test_traffic_formatter_selects_readable_units(
    value: int, expected: str
) -> None:
    assert TrafficFormatter.bytes(value) == expected


def test_traffic_formatter_uses_remote_limit() -> None:
    assert TrafficFormatter.format(
        int(2.4 * 1024**3), 600 * 1024**3
    ) == "2.4 ГБ / 600 ГБ"
    assert TrafficFormatter.format(None, None) == "Данные обновляются..."
    assert TrafficFormatter.format(1, 0) == "Без ограничений"


@pytest.mark.asyncio
async def test_successful_payment_credits_balance_once() -> None:
    owner = user(1)
    order_id = uuid.uuid4()
    order = Order(
        id=order_id,
        user_id=owner.id,
        tariff_id=1,
        status=OrderStatus.awaiting_payment,
        tariff_name_snapshot="VPN",
        duration_days_snapshot=30,
        traffic_limit_gb_snapshot=600,
        is_unlimited_traffic_snapshot=False,
        device_limit_snapshot=3,
        amount_snapshot=Decimal("500.00"),
        currency_snapshot="RUB",
        original_amount=Decimal("500.00"),
        discount_amount=Decimal("0.00"),
        final_amount=Decimal("500.00"),
        bonus_days=0,
    )
    payment = Payment(
        id=uuid.uuid4(),
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="yookassa-payment",
        status=PaymentStatus.pending,
        amount=Decimal("500.00"),
        currency="RUB",
        payment_url="https://yookassa.example/payment",
        idempotency_key="payment-create",
    )
    subscription = Subscription(
        id=uuid.uuid4(),
        user_id=owner.id,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.active,
        provisioning_status=ProvisioningStatus.active,
        started_at=datetime.now(UTC),
        expires_at=datetime.now(UTC) + timedelta(days=30),
        device_limit=3,
    )
    session = session_with_scalars(payment, order, owner, None)
    session.get = AsyncMock(return_value=owner)
    subscription_service = MagicMock()
    subscription_service.extend_from_paid_order = AsyncMock(
        return_value=subscription
    )

    result = await PaymentService(
        session,
        OnliPayClient(),
        subscription_service=subscription_service,
    ).process_confirmed_payment(
        provider_payment_id=payment.provider_payment_id,
        reported_order_id=str(order.id),
        amount=payment.amount,
        currency=payment.currency,
    )

    assert result.completed is True
    assert result.balance_after == Decimal("500.00")
    assert owner.balance == Decimal("500.00")
    balance_entries = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], BalanceTransaction)
    ]
    assert len(balance_entries) == 1
    assert balance_entries[0].type == BalanceTransactionType.topup


def test_referral_code_cannot_be_changed() -> None:
    owner = user(1)
    with pytest.raises(ValueError, match="immutable"):
        owner.referral_code = "replacement"
