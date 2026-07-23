"""switch balance billing to fixed-term subscription purchases

Revision ID: 20260723_0007
Revises: 20260723_0006
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260723_0007"
down_revision: str | None = "20260723_0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

ORDER_PURPOSES = ("subscription_purchase", "wallet_topup")


def upgrade() -> None:
    bind = op.get_bind()
    op.execute(
        "ALTER TYPE balance_transaction_type "
        "ADD VALUE IF NOT EXISTS 'subscription_purchase'"
    )

    order_purpose = postgresql.ENUM(*ORDER_PURPOSES, name="order_purpose")
    order_purpose.create(bind, checkfirst=True)
    op.add_column(
        "orders",
        sa.Column(
            "purpose",
            postgresql.ENUM(
                *ORDER_PURPOSES,
                name="order_purpose",
                create_type=False,
            ),
            server_default="subscription_purchase",
            nullable=False,
        ),
    )
    op.create_index("ix_orders_purpose", "orders", ["purpose"])
    op.alter_column(
        "orders",
        "tariff_id",
        existing_type=sa.Integer(),
        nullable=True,
    )

    op.add_column(
        "balance_transactions",
        sa.Column("tariff_id", sa.Integer(), nullable=True),
    )
    op.add_column(
        "balance_transactions",
        sa.Column("order_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "balance_transactions",
        sa.Column("description", sa.String(255), nullable=True),
    )
    op.create_foreign_key(
        "fk_balance_transactions_tariff_id",
        "balance_transactions",
        "tariffs",
        ["tariff_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_foreign_key(
        "fk_balance_transactions_order_id",
        "balance_transactions",
        "orders",
        ["order_id"],
        ["id"],
        ondelete="SET NULL",
    )

    for column in (
        "expiry_notice_3d_at",
        "expiry_notice_1d_at",
        "expired_notice_at",
    ):
        op.add_column(
            "subscriptions",
            sa.Column(column, sa.DateTime(timezone=True), nullable=True),
        )

def downgrade() -> None:
    raise RuntimeError(
        "Destructive database downgrades are disabled for BlazeVPN"
    )
    for column in (
        "expired_notice_at",
        "expiry_notice_1d_at",
        "expiry_notice_3d_at",
    ):
        op.drop_column("subscriptions", column)

    op.drop_constraint(
        "fk_balance_transactions_order_id",
        "balance_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_balance_transactions_tariff_id",
        "balance_transactions",
        type_="foreignkey",
    )
    op.drop_column("balance_transactions", "description")
    op.drop_column("balance_transactions", "order_id")
    op.drop_column("balance_transactions", "tariff_id")

    op.drop_index("ix_orders_purpose", table_name="orders")
    op.drop_column("orders", "purpose")
    postgresql.ENUM(*ORDER_PURPOSES, name="order_purpose").drop(
        op.get_bind(), checkfirst=True
    )
    # PostgreSQL enum values are intentionally retained: removing a value can
    # invalidate historical balance rows and is not a safe automatic downgrade.
    # tariff_id also remains nullable so wallet top-up orders are not destroyed.
