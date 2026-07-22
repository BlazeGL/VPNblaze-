"""Create tariffs and orders and seed initial tariffs.

Revision ID: 20260722_0002
Revises: 20260721_0001
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0002"
down_revision: str | None = "20260721_0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_STATUSES = (
    "pending",
    "awaiting_payment",
    "paid",
    "processing",
    "completed",
    "cancelled",
    "expired",
    "failed",
)


def upgrade() -> None:
    op.create_table(
        "tariffs",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("duration_days", sa.Integer(), nullable=False),
        sa.Column("price", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), server_default="RUB", nullable=False),
        sa.Column("traffic_limit_gb", sa.Integer(), nullable=True),
        sa.Column(
            "is_unlimited_traffic", sa.Boolean(), server_default="false", nullable=False
        ),
        sa.Column("device_limit", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("sort_order", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "duration_days > 0", name=op.f("ck_tariffs_duration_positive")
        ),
        sa.CheckConstraint("price > 0", name=op.f("ck_tariffs_price_positive")),
        sa.CheckConstraint(
            "device_limit > 0", name=op.f("ck_tariffs_device_limit_positive")
        ),
        sa.CheckConstraint(
            "traffic_limit_gb > 0 OR "
            "(is_unlimited_traffic AND traffic_limit_gb IS NULL)",
            name=op.f("ck_tariffs_traffic_limit_valid"),
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_tariffs")),
    )
    op.create_index(op.f("ix_tariffs_is_active"), "tariffs", ["is_active"])

    status_enum = postgresql.ENUM(*ORDER_STATUSES, name="order_status")
    status_enum.create(op.get_bind(), checkfirst=True)
    op.create_table(
        "orders",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("tariff_id", sa.Integer(), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(*ORDER_STATUSES, name="order_status", create_type=False),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("tariff_name_snapshot", sa.String(255), nullable=False),
        sa.Column("duration_days_snapshot", sa.Integer(), nullable=False),
        sa.Column("traffic_limit_gb_snapshot", sa.Integer(), nullable=True),
        sa.Column("is_unlimited_traffic_snapshot", sa.Boolean(), nullable=False),
        sa.Column("device_limit_snapshot", sa.Integer(), nullable=False),
        sa.Column("amount_snapshot", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency_snapshot", sa.String(3), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("paid_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("failure_reason", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["tariff_id"],
            ["tariffs.id"],
            name=op.f("fk_orders_tariff_id_tariffs"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_orders_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_orders")),
    )
    op.create_index(op.f("ix_orders_status"), "orders", ["status"])
    op.create_index(op.f("ix_orders_tariff_id"), "orders", ["tariff_id"])
    op.create_index(op.f("ix_orders_user_id"), "orders", ["user_id"])

    tariffs = sa.table(
        "tariffs",
        sa.column("name", sa.String),
        sa.column("description", sa.Text),
        sa.column("duration_days", sa.Integer),
        sa.column("price", sa.Numeric),
        sa.column("currency", sa.String),
        sa.column("traffic_limit_gb", sa.Integer),
        sa.column("is_unlimited_traffic", sa.Boolean),
        sa.column("device_limit", sa.Integer),
        sa.column("is_active", sa.Boolean),
        sa.column("sort_order", sa.Integer),
    )
    if op.get_bind().scalar(sa.select(sa.func.count()).select_from(tariffs)) == 0:
        op.bulk_insert(
            tariffs,
            [
                {
                    "name": "1 месяц",
                    "description": None,
                    "duration_days": 30,
                    "price": "199.00",
                    "currency": "RUB",
                    "traffic_limit_gb": 200,
                    "is_unlimited_traffic": False,
                    "device_limit": 3,
                    "is_active": True,
                    "sort_order": 10,
                },
                {
                    "name": "3 месяца",
                    "description": None,
                    "duration_days": 90,
                    "price": "499.00",
                    "currency": "RUB",
                    "traffic_limit_gb": 600,
                    "is_unlimited_traffic": False,
                    "device_limit": 5,
                    "is_active": True,
                    "sort_order": 20,
                },
                {
                    "name": "6 месяцев",
                    "description": None,
                    "duration_days": 180,
                    "price": "899.00",
                    "currency": "RUB",
                    "traffic_limit_gb": 1200,
                    "is_unlimited_traffic": False,
                    "device_limit": 5,
                    "is_active": True,
                    "sort_order": 30,
                },
                {
                    "name": "12 месяцев",
                    "description": None,
                    "duration_days": 365,
                    "price": "1499.00",
                    "currency": "RUB",
                    "traffic_limit_gb": None,
                    "is_unlimited_traffic": True,
                    "device_limit": 7,
                    "is_active": True,
                    "sort_order": 40,
                },
            ],
        )


def downgrade() -> None:
    op.drop_index(op.f("ix_orders_user_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_tariff_id"), table_name="orders")
    op.drop_index(op.f("ix_orders_status"), table_name="orders")
    op.drop_table("orders")
    postgresql.ENUM(*ORDER_STATUSES, name="order_status").drop(
        op.get_bind(), checkfirst=True
    )
    op.drop_index(op.f("ix_tariffs_is_active"), table_name="tariffs")
    op.drop_table("tariffs")
