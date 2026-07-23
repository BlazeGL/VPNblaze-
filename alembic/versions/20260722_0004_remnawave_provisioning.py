"""stage 4 remnawave provisioning

Revision ID: 20260722_0004
Revises: 20260722_0003
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0004"
down_revision: str | None = "20260722_0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

PROVISIONING_STATUSES = (
    "not_started",
    "pending",
    "provisioning",
    "active",
    "failed",
    "disabled",
)
OPERATION_STATUSES = ("pending", "processing", "completed", "failed")


def upgrade() -> None:
    bind = op.get_bind()
    provisioning_status = postgresql.ENUM(
        *PROVISIONING_STATUSES, name="provisioning_status"
    )
    operation_status = postgresql.ENUM(
        *OPERATION_STATUSES, name="provisioning_operation_status"
    )
    provisioning_status.create(bind, checkfirst=True)
    operation_status.create(bind, checkfirst=True)

    op.add_column("subscriptions", sa.Column("remnawave_user_uuid", sa.String(36)))
    op.add_column("subscriptions", sa.Column("remnawave_username", sa.String(36)))
    op.add_column("subscriptions", sa.Column("remnawave_short_uuid", sa.String(64)))
    op.add_column("subscriptions", sa.Column("remnawave_status", sa.String(20)))
    op.add_column(
        "subscriptions", sa.Column("remnawave_last_sync_at", sa.DateTime(timezone=True))
    )
    op.add_column("subscriptions", sa.Column("remnawave_sync_error", sa.Text()))
    op.add_column(
        "subscriptions", sa.Column("remnawave_created_at", sa.DateTime(timezone=True))
    )
    op.add_column(
        "subscriptions", sa.Column("remnawave_internal_squad_uuid", sa.String(36))
    )
    op.add_column(
        "subscriptions",
        sa.Column(
            "provisioning_status",
            postgresql.ENUM(
                *PROVISIONING_STATUSES,
                name="provisioning_status",
                create_type=False,
            ),
            server_default="not_started",
            nullable=False,
        ),
    )
    op.add_column("subscriptions", sa.Column("used_traffic_bytes", sa.Integer()))
    op.add_column("subscriptions", sa.Column("connected_devices", sa.Integer()))
    op.add_column(
        "subscriptions", sa.Column("next_retry_at", sa.DateTime(timezone=True))
    )
    op.create_unique_constraint(
        "uq_subscriptions_remnawave_user_uuid",
        "subscriptions",
        ["remnawave_user_uuid"],
    )
    op.create_unique_constraint(
        "uq_subscriptions_remnawave_username",
        "subscriptions",
        ["remnawave_username"],
    )
    op.create_index(
        "ix_subscriptions_remnawave_user_uuid",
        "subscriptions",
        ["remnawave_user_uuid"],
    )
    op.create_index(
        "ix_subscriptions_remnawave_username",
        "subscriptions",
        ["remnawave_username"],
    )
    op.create_index(
        "ix_subscriptions_provisioning_status",
        "subscriptions",
        ["provisioning_status"],
    )
    op.create_index(
        "ix_subscriptions_next_retry_at", "subscriptions", ["next_retry_at"]
    )

    op.create_table(
        "provisioning_operations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("subscription_id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid()),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("source", sa.String(20), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *OPERATION_STATUSES,
                name="provisioning_operation_status",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("attempts", sa.Integer(), server_default="0", nullable=False),
        sa.Column("next_retry_at", sa.DateTime(timezone=True)),
        sa.Column("last_error", sa.Text()),
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
        sa.Column("completed_at", sa.DateTime(timezone=True)),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["subscription_id"], ["subscriptions.id"], ondelete="CASCADE"
        ),
        sa.ForeignKeyConstraint(["order_id"], ["orders.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("order_id", name="uq_provisioning_operations_order_id"),
        sa.UniqueConstraint("idempotency_key", name="uq_provisioning_operations_key"),
    )
    for column in (
        "user_id",
        "subscription_id",
        "order_id",
        "status",
        "next_retry_at",
    ):
        op.create_index(
            f"ix_provisioning_operations_{column}",
            "provisioning_operations",
            [column],
        )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive database downgrades are disabled for BlazeVPN"
    )
    for column in (
        "next_retry_at",
        "status",
        "order_id",
        "subscription_id",
        "user_id",
    ):
        op.drop_index(
            f"ix_provisioning_operations_{column}",
            table_name="provisioning_operations",
        )
    op.drop_table("provisioning_operations")
    op.drop_index("ix_subscriptions_next_retry_at", table_name="subscriptions")
    op.drop_index("ix_subscriptions_provisioning_status", table_name="subscriptions")
    op.drop_index("ix_subscriptions_remnawave_username", table_name="subscriptions")
    op.drop_index("ix_subscriptions_remnawave_user_uuid", table_name="subscriptions")
    op.drop_constraint(
        "uq_subscriptions_remnawave_username", "subscriptions", type_="unique"
    )
    op.drop_constraint(
        "uq_subscriptions_remnawave_user_uuid", "subscriptions", type_="unique"
    )
    for column in (
        "next_retry_at",
        "connected_devices",
        "used_traffic_bytes",
        "provisioning_status",
        "remnawave_internal_squad_uuid",
        "remnawave_created_at",
        "remnawave_sync_error",
        "remnawave_last_sync_at",
        "remnawave_status",
        "remnawave_short_uuid",
        "remnawave_username",
        "remnawave_user_uuid",
    ):
        op.drop_column("subscriptions", column)
    bind = op.get_bind()
    postgresql.ENUM(*OPERATION_STATUSES, name="provisioning_operation_status").drop(
        bind, checkfirst=True
    )
    postgresql.ENUM(*PROVISIONING_STATUSES, name="provisioning_status").drop(
        bind, checkfirst=True
    )
