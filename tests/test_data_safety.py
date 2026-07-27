import ast
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database import startup as startup_module
from app.database.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    Tariff,
    User,
)
from app.database.startup import ensure_initial_data

ROOT = Path(__file__).parents[1]
MIGRATIONS = sorted((ROOT / "alembic" / "versions").glob("*.py"))


def function_source(path: Path, name: str) -> str:
    source = path.read_text(encoding="utf-8")
    tree = ast.parse(source)
    function = next(
        node
        for node in tree.body
        if isinstance(node, ast.FunctionDef) and node.name == name
    )
    return ast.get_source_segment(source, function) or ""


def test_upgrade_migrations_have_no_destructive_operations() -> None:
    forbidden = (
        "op.drop_",
        "drop table",
        "delete from",
        "truncate ",
        "metadata.drop",
    )
    for migration in MIGRATIONS:
        upgrade = function_source(migration, "upgrade").lower()
        for operation in forbidden:
            assert operation not in upgrade, f"{migration.name}: {operation}"


def test_all_destructive_downgrades_are_blocked() -> None:
    for migration in MIGRATIONS:
        source = migration.read_text(encoding="utf-8")
        tree = ast.parse(source)
        downgrade = next(
            node
            for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "downgrade"
        )
        assert isinstance(downgrade.body[0], ast.Raise), migration.name


def test_billing_migration_does_not_rewrite_business_data() -> None:
    migration = ROOT / "alembic" / "versions" / (
        "20260723_0007_fixed_term_billing.py"
    )
    upgrade = function_source(migration, "upgrade").lower()
    assert "update tariffs" not in upgrade
    assert "update subscriptions" not in upgrade


def test_compose_pins_postgres_volume_and_uses_startup_guard() -> None:
    compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")
    assert "name: ${POSTGRES_VOLUME_NAME:-vpntg_postgres_data}" in compose
    assert 'command: ["python", "-m", "app.database.startup"]' in compose
    assert "./.db-state:/var/lib/blazevpn-state" in compose
    assert "down -v" not in compose


def persisted_records() -> tuple[
    User, Tariff, Order, Payment, Subscription
]:
    now = datetime(2026, 7, 23, tzinfo=UTC)
    user = User(
        id=1,
        telegram_id=777,
        username="existing",
        balance=Decimal("321.45"),
        referral_code="persistent-referral",
    )
    tariff = Tariff(
        id=1,
        name="BlazeVPN — 30 дней",
        duration_days=30,
        price=Decimal("99.00"),
        traffic_limit_gb=200,
        is_unlimited_traffic=False,
        device_limit=3,
    )
    order = Order(
        user_id=1,
        tariff_id=1,
        status=OrderStatus.completed,
        tariff_name_snapshot=tariff.name,
        duration_days_snapshot=30,
        traffic_limit_gb_snapshot=200,
        is_unlimited_traffic_snapshot=False,
        device_limit_snapshot=3,
        amount_snapshot=Decimal("99.00"),
        currency_snapshot="RUB",
        original_amount=Decimal("99.00"),
        final_amount=Decimal("99.00"),
    )
    payment = Payment(
        order_id=order.id,
        provider_payment_id="payment-history",
        status=PaymentStatus.paid,
        amount=Decimal("99.00"),
        currency="RUB",
        payment_url="https://example.test/payment",
        idempotency_key="payment-history",
        paid_at=now,
    )
    subscription = Subscription(
        user_id=1,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.active,
        started_at=now,
        expires_at=now + timedelta(days=30),
        tariff_id=1,
        order_id=order.id,
        traffic_limit_gb=200,
        device_limit=3,
    )
    return user, tariff, order, payment, subscription


@pytest.mark.asyncio
async def test_repeated_startup_preserves_all_user_business_data() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=1)
    session.flush = AsyncMock()
    user, tariff, order, payment, subscription = persisted_records()
    before = (
        user.telegram_id,
        user.balance,
        user.username,
        tariff.price,
        order.status,
        order.amount_snapshot,
        payment.status,
        payment.amount,
        subscription.status,
        subscription.expires_at,
    )

    first = await ensure_initial_data(session)
    second = await ensure_initial_data(session)

    after = (
        user.telegram_id,
        user.balance,
        user.username,
        tariff.price,
        order.status,
        order.amount_snapshot,
        payment.status,
        payment.amount,
        subscription.status,
        subscription.expires_at,
    )
    assert first is False
    assert second is False
    assert after == before
    session.scalar.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_empty_database_startup_does_not_create_a_tariff() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.flush = AsyncMock()

    created = await ensure_initial_data(session)

    assert created is False
    session.scalar.assert_not_awaited()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()


@pytest.mark.asyncio
async def test_startup_refuses_silent_database_replacement(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    settings = MagicMock(database_url="postgresql+asyncpg://unused")
    monkeypatch.setattr(startup_module, "get_settings", lambda: settings)
    monkeypatch.setattr(startup_module, "state_file_exists", lambda _path: True)
    monkeypatch.setattr(
        startup_module,
        "database_has_application_schema",
        AsyncMock(return_value=False),
    )
    upgrade = MagicMock()
    monkeypatch.setattr(startup_module, "run_alembic_upgrade", upgrade)
    monkeypatch.setenv("DATABASE_STATE_FILE", "unused-state-file")

    with pytest.raises(RuntimeError, match="Refusing to initialize"):
        await startup_module.startup()

    upgrade.assert_not_called()
