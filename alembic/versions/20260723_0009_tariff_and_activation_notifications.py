"""canonical tariff and idempotent activation notifications

Revision ID: 20260723_0009
Revises: 20260723_0008
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260723_0009"
down_revision: str | None = "20260723_0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("activation_notified_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_subscriptions_activation_notified_at",
        "subscriptions",
        ["activation_notified_at"],
    )

    # A completed provisioning operation is the durable proof that access was
    # activated. Repair stale pending rows before the retry worker starts.
    op.execute(
        """
        UPDATE subscriptions AS subscription
        SET status = 'active',
            provisioning_status = 'active',
            last_activation_error = NULL,
            next_retry_at = NULL
        WHERE EXISTS (
            SELECT 1
            FROM provisioning_operations AS operation
            WHERE operation.subscription_id = subscription.id
              AND operation.status = 'completed'
        )
        """
    )
    # Existing active users have already received the legacy notification.
    # Backfilling prevents a deployment-wide resend.
    op.execute(
        """
        UPDATE subscriptions
        SET activation_notified_at = COALESCE(updated_at, created_at, now())
        WHERE status = 'active'
           OR provisioning_status = 'active'
        """
    )

    # Preserve orders and their tariff foreign keys. If both old and new plans
    # exist, one canonical row remains active and every historical row remains.
    op.execute(
        """
        INSERT INTO tariffs (
            name,
            description,
            duration_days,
            price,
            currency,
            traffic_limit_gb,
            is_unlimited_traffic,
            device_limit,
            is_active,
            sort_order
        )
        SELECT
            'BlazeVPN — 30 дней',
            NULL,
            30,
            99.00,
            'RUB',
            600,
            false,
            3,
            true,
            10
        WHERE NOT EXISTS (
            SELECT 1
            FROM tariffs
            WHERE duration_days = 30
              AND currency = 'RUB'
              AND (
                  price IN (99.00, 199.00)
                  OR name = 'BlazeVPN — 30 дней'
              )
        )
        """
    )
    op.execute(
        """
        WITH canonical AS (
            SELECT id
            FROM tariffs
            WHERE duration_days = 30
              AND currency = 'RUB'
              AND (
                  price IN (99.00, 199.00)
                  OR name = 'BlazeVPN — 30 дней'
              )
            ORDER BY
                CASE WHEN price = 99.00 THEN 0 ELSE 1 END,
                id
            LIMIT 1
        )
        UPDATE tariffs
        SET name = 'BlazeVPN — 30 дней',
            description = NULL,
            duration_days = 30,
            price = 99.00,
            currency = 'RUB',
            traffic_limit_gb = 600,
            is_unlimited_traffic = false,
            device_limit = 3,
            is_active = true,
            sort_order = 10
        WHERE id = (SELECT id FROM canonical)
        """
    )
    op.execute(
        """
        WITH canonical AS (
            SELECT id
            FROM tariffs
            WHERE name = 'BlazeVPN — 30 дней'
              AND duration_days = 30
              AND price = 99.00
              AND currency = 'RUB'
            ORDER BY id
            LIMIT 1
        )
        UPDATE tariffs
        SET is_active = false
        WHERE id <> (SELECT id FROM canonical)
          AND is_active = true
        """
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive database downgrades are disabled for BlazeVPN"
    )
