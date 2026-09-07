import uuid
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    Order,
    OrderPurpose,
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
    now = datetime.now(UTC)
    return Subscription(
        id=uuid.uuid4(),
        user_id=user_id,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.active,
        provisioning_status=ProvisioningStatus.active,
        started_at=now,
        expires_at=now + timedelta(days=30),
        device_limit=1,
    )


def subscription_order(user_id: int = 1) -> Order:
    return Order(
        id=uuid.uuid4(),
        user_id=user_id,
        tariff_id=1,
        status=OrderStatus.pending,
        tariff_name_snapshot="BlazeVPN — 30 дней",
        duration_days_snapshot=30,
        traffic_limit_gb_snapshot=600,
        is_unlimited_traffic_snapshot=False,
        device_limit_snapshot=3,
        amount_snapshot=Decimal("99.00"),
        currency_snapshot="RUB",
        original_amount=Decimal("99.00"),
        discount_amount=Decimal("0.00"),
        final_amount=Decimal("99.00"),
        bonus_days=0,
    )


async def purchase(balance: str) -> tuple[object, User, MagicMock]:
    owner = user(1, balance=balance)
    order = subscription_order()
    subscription = paid_subscription()
    session = session_with_scalars(order, owner, None)
    subscription_service = MagicMock()
    subscription_service.extend_from_paid_order = AsyncMock(
        return_value=subscription
    )
    result = await BillingService(
        session, subscription_service
    ).purchase_order(
        order.id,
        user_id=owner.id,
    )
    return result, owner, session


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("balance", "remaining"),
    [("99.00", "0.00"), ("100.00", "1.00")],
)
async def test_subscription_purchase_debits_exactly_99(
    balance: str, remaining: str
) -> None:
    result, owner, session = await purchase(balance)

    assert result.purchased is True
    assert owner.balance == Decimal(remaining)
    transaction = session.add.call_args.args[0]
    assert transaction.type == BalanceTransactionType.subscription_purchase
    assert transaction.amount == Decimal("-99.00")
    assert transaction.balance_before == Decimal(balance)
    assert transaction.balance_after == Decimal(remaining)
    assert transaction.tariff_id == 1
    assert transaction.order_id == result.order.id


@pytest.mark.asyncio
async def test_insufficient_balance_does_not_buy_or_disable() -> None:
    owner = user(1, balance="98.00")
    order = subscription_order()
    session = session_with_scalars(order, owner, None)
    subscription_service = MagicMock()

    result = await BillingService(
        session, subscription_service
    ).purchase_order(
        order.id,
        user_id=owner.id,
    )

    assert result.purchased is False
    assert result.shortfall == Decimal("1.00")
    assert owner.balance == Decimal("98.00")
    assert order.status == OrderStatus.pending
    session.add.assert_not_called()
    subscription_service.extend_from_paid_order.assert_not_called()


@pytest.mark.asyncio
async def test_repeated_purchase_request_does_not_debit_twice() -> None:
    owner = user(1, balance="99.00")
    order = subscription_order()
    subscription = paid_subscription()
    session = session_with_scalars(
        order,
        owner,
        None,
        order,
        owner,
        subscription,
    )
    subscription_service = MagicMock()
    subscription_service.extend_from_paid_order = AsyncMock(
        return_value=subscription
    )
    service = BillingService(session, subscription_service)

    first = await service.purchase_order(order.id, user_id=owner.id)
    second = await service.purchase_order(order.id, user_id=owner.id)

    assert first.purchased is True
    assert second.purchased is True
    assert second.already_processed is True
    assert owner.balance == Decimal("0.00")
    balance_entries = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], BalanceTransaction)
    ]
    assert len(balance_entries) == 1


@pytest.mark.asyncio
async def test_two_referral_bonuses_can_pay_for_subscription() -> None:
    result, owner, _ = await purchase("100.00")

    assert result.purchased is True
    assert owner.balance == Decimal("1.00")


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
    session = session_with_scalars(payment, order, None, None)
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
    assert result.balance_after == Decimal("0.00")
    assert owner.balance == Decimal("0.00")
    balance_entries = [
        call.args[0]
        for call in session.add.call_args_list
        if isinstance(call.args[0], BalanceTransaction)
    ]
    assert len(balance_entries) == 2
    assert balance_entries[0].type == BalanceTransactionType.topup
    assert balance_entries[1].type == BalanceTransactionType.subscription_purchase
    assert balance_entries[1].amount == Decimal("-500.00")


@pytest.mark.asyncio
async def test_wallet_topup_does_not_activate_subscription() -> None:
    owner = user(1, balance="50.00")
    order = Order(
        id=uuid.uuid4(),
        user_id=owner.id,
        tariff_id=None,
        purpose=OrderPurpose.wallet_topup,
        status=OrderStatus.awaiting_payment,
        tariff_name_snapshot="Пополнение баланса",
        duration_days_snapshot=0,
        traffic_limit_gb_snapshot=None,
        is_unlimited_traffic_snapshot=False,
        device_limit_snapshot=1,
        amount_snapshot=Decimal("49.00"),
        currency_snapshot="RUB",
        original_amount=Decimal("49.00"),
        discount_amount=Decimal("0.00"),
        final_amount=Decimal("49.00"),
        bonus_days=0,
    )
    payment = Payment(
        id=uuid.uuid4(),
        order_id=order.id,
        provider="yookassa",
        provider_payment_id="wallet-topup",
        status=PaymentStatus.pending,
        amount=Decimal("49.00"),
        currency="RUB",
        payment_url="https://yookassa.example/payment",
        idempotency_key="wallet-topup-create",
    )
    session = session_with_scalars(payment, order, None)
    session.get = AsyncMock(return_value=owner)
    subscription_service = MagicMock()

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
    assert result.subscription is None
    assert result.balance_after == Decimal("99.00")
    assert owner.balance == Decimal("99.00")
    subscription_service.extend_from_paid_order.assert_not_called()


def test_referral_code_cannot_be_changed() -> None:
    owner = user(1)
    with pytest.raises(ValueError, match="immutable"):
        owner.referral_code = "replacement"
