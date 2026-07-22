from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.bot.filters import AdminFilter
from app.database.models import Order, OrderStatus, Tariff
from app.database.repositories import (
    OrderOwnershipError,
    OrderRepository,
    TariffRepository,
)


def session_mock() -> MagicMock:
    session = MagicMock()
    session.flush = AsyncMock()
    session.get = AsyncMock()
    session.scalar = AsyncMock()
    session.scalars = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_create_tariff() -> None:
    session = session_mock()
    tariff = await TariffRepository(session).create(
        name="Test",
        duration_days=30,
        price=Decimal("199.00"),
        traffic_limit_gb=200,
        device_limit=3,
    )
    assert tariff.name == "Test"
    assert tariff.price == Decimal("199.00")
    session.add.assert_called_once_with(tariff)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_active_tariffs() -> None:
    session = session_mock()
    expected = [
        Tariff(id=1, name="Active", duration_days=30, price=199, device_limit=3)
    ]
    session.scalars.return_value = expected
    result = await TariffRepository(session).get_active()
    assert result == expected


def make_tariff() -> Tariff:
    return Tariff(
        id=7,
        name="3 месяца",
        duration_days=90,
        price=Decimal("499.00"),
        currency="RUB",
        traffic_limit_gb=600,
        is_unlimited_traffic=False,
        device_limit=5,
    )


@pytest.mark.asyncio
async def test_create_order_copies_snapshot() -> None:
    session = session_mock()
    tariff = make_tariff()
    order = await OrderRepository(session).create_from_tariff(11, tariff)
    assert order.tariff_name_snapshot == "3 месяца"
    assert order.amount_snapshot == Decimal("499.00")
    assert order.traffic_limit_gb_snapshot == 600
    assert order.status == OrderStatus.pending


@pytest.mark.asyncio
async def test_tariff_change_does_not_change_order_snapshot() -> None:
    session = session_mock()
    tariff = make_tariff()
    order = await OrderRepository(session).create_from_tariff(11, tariff)
    tariff.name = "Новое имя"
    tariff.price = Decimal("999.00")
    assert order.tariff_name_snapshot == "3 месяца"
    assert order.amount_snapshot == Decimal("499.00")


@pytest.mark.asyncio
async def test_user_cannot_cancel_foreign_order() -> None:
    session = session_mock()
    order = Order(user_id=2, tariff_id=7, status=OrderStatus.pending)
    repository = OrderRepository(session)
    repository.get_by_id = AsyncMock(return_value=order)
    with pytest.raises(OrderOwnershipError):
        await repository.cancel("d28ad9c6-03c8-45ac-892f-e6bb51eab759", user_id=1)
    assert order.status == OrderStatus.pending


@pytest.mark.asyncio
async def test_repeated_cancellation_is_idempotent() -> None:
    session = session_mock()
    order = Order(user_id=1, tariff_id=7, status=OrderStatus.cancelled)
    repository = OrderRepository(session)
    repository.get_by_id = AsyncMock(return_value=order)
    result = await repository.cancel("d28ad9c6-03c8-45ac-892f-e6bb51eab759", user_id=1)
    assert result.status == OrderStatus.cancelled
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_admin_filter() -> None:
    event = SimpleNamespace(from_user=SimpleNamespace(id=42))
    admin_filter = AdminFilter()
    assert await admin_filter(event, {42}) is True
    assert await admin_filter(event, {7}) is False
