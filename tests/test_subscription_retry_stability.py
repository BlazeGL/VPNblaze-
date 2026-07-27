import uuid
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.handlers.remnawave_admin import retry_failed
from app.database.models import ProvisioningStatus, SubscriptionStatus
from app.integrations.remnawave.exceptions import RemnawaveNetworkError
from app.workers import subscription_retry


def session_context(session: MagicMock) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=session)
    context.__aexit__ = AsyncMock(return_value=None)
    return context


def transactional_context(session: MagicMock) -> MagicMock:
    session.begin.return_value = session_context(session)
    return session_context(session)


def test_renewal_offer_uses_subscription_tariff_price() -> None:
    tariff = SimpleNamespace(
        is_active=True,
        price=Decimal("349.00"),
        currency="RUB",
        duration_days=90,
    )
    text = subscription_retry._renewal_offer_text(tariff)
    assert "349" in text
    assert "90" in text


@pytest.mark.parametrize("tariff", [None, SimpleNamespace(is_active=False)])
def test_renewal_offer_hides_price_without_active_tariff(tariff: object) -> None:
    text = subscription_retry._renewal_offer_text(tariff)  # type: ignore[arg-type]
    assert "Стоимость продления" not in text
    assert "Тарифы" in text


@pytest.mark.asyncio
async def test_manual_retry_resets_exhausted_activation_attempts() -> None:
    item = SimpleNamespace(
        provisioning_status=ProvisioningStatus.failed,
        activation_attempts=5,
        next_retry_at=None,
    )
    session = MagicMock()
    session.scalars = AsyncMock(return_value=[item])
    factory = MagicMock(return_value=transactional_context(session))
    callback = MagicMock()
    callback.answer = AsyncMock()

    await retry_failed(callback, factory)

    assert item.provisioning_status == ProvisioningStatus.pending
    assert item.activation_attempts == 0
    assert item.next_retry_at is not None
    callback.answer.assert_awaited_once_with("Поставлено в очередь: 1", show_alert=True)


@pytest.mark.asyncio
async def test_paid_retry_skips_subscription_after_attempt_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    order_id = uuid.uuid4()
    initial_session = MagicMock()
    initial_session.scalars = AsyncMock(return_value=["payment-1"])

    payment = SimpleNamespace(order_id=order_id)
    order = SimpleNamespace(id=order_id, user_id=7)
    exhausted = SimpleNamespace(
        status=SubscriptionStatus.activation_failed,
        activation_attempts=5,
        next_retry_at=None,
    )
    item_session = MagicMock()
    item_session.scalar = AsyncMock(side_effect=[payment, exhausted])
    item_session.get = AsyncMock(return_value=order)

    factory = MagicMock(
        side_effect=[
            session_context(initial_session),
            transactional_context(item_session),
        ]
    )
    payment_service = MagicMock()
    monkeypatch.setattr(subscription_retry, "PaymentService", payment_service)

    await subscription_retry._retry_paid_orders(
        factory,
        MagicMock(),
        set(),
        None,
        None,
        None,
        None,
    )

    payment_service.assert_not_called()


@pytest.mark.asyncio
async def test_expiration_continues_after_one_remnawave_failure() -> None:
    first_id = uuid.uuid4()
    second_id = uuid.uuid4()
    initial_session = MagicMock()
    initial_session.scalars = AsyncMock(return_value=[first_id, second_id])

    first = SimpleNamespace(
        id=first_id,
        user_id=1,
        status=SubscriptionStatus.active,
        provisioning_status=ProvisioningStatus.active,
        remnawave_user_uuid=str(uuid.uuid4()),
        remnawave_status="ACTIVE",
    )
    second = SimpleNamespace(
        id=second_id,
        user_id=2,
        status=SubscriptionStatus.active,
        provisioning_status=ProvisioningStatus.active,
        remnawave_user_uuid=str(uuid.uuid4()),
        remnawave_status="ACTIVE",
    )
    first_session = MagicMock()
    first_session.scalar = AsyncMock(return_value=first)
    second_session = MagicMock()
    second_session.scalar = AsyncMock(return_value=second)
    factory = MagicMock(
        side_effect=[
            session_context(initial_session),
            transactional_context(first_session),
            transactional_context(second_session),
        ]
    )
    client = MagicMock()
    client.disable_user = AsyncMock(
        side_effect=[RemnawaveNetworkError("temporary failure"), MagicMock()]
    )

    await subscription_retry._disable_expired(factory, client)

    assert client.disable_user.await_count == 2
    assert first.status == SubscriptionStatus.active
    assert second.status == SubscriptionStatus.expired
    assert second.provisioning_status == ProvisioningStatus.disabled
