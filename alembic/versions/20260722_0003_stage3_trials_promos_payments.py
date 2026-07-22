"""Add trials, subscriptions, promo codes, payments and audit logs.

Revision ID: 20260722_0003
Revises: 20260722_0002
Create Date: 2026-07-22
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260722_0003"
down_revision: str | None = "20260722_0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SUBSCRIPTION_SOURCES = ("trial", "paid", "promo", "admin")
SUBSCRIPTION_STATUSES = (
    "pending",
    "active",
    "expired",
    "disabled",
    "activation_failed",
)
PROMO_TYPES = ("percent", "fixed", "bonus_days")
PAYMENT_STATUSES = (
    "created",
    "pending",
    "paid",
    "failed",
    "cancelled",
    "expired",
    "refunded",
)


def upgrade() -> None:
    subscription_source = postgresql.ENUM(
        *SUBSCRIPTION_SOURCES, name="subscription_source_type"
    )
    subscription_status = postgresql.ENUM(
        *SUBSCRIPTION_STATUSES, name="subscription_status"
    )
    promo_type = postgresql.ENUM(*PROMO_TYPES, name="promo_discount_type")
    payment_status = postgresql.ENUM(*PAYMENT_STATUSES, name="payment_status")
    bind = op.get_bind()
    subscription_source.create(bind, checkfirst=True)
    subscription_status.create(bind, checkfirst=True)
    promo_type.create(bind, checkfirst=True)
    payment_status.create(bind, checkfirst=True)

    op.add_column(
        "users",
        sa.Column("trial_used", sa.Boolean(), server_default="false", nullable=False),
    )
    op.add_column(
        "users",
        sa.Column(
            "trial_disabled", sa.Boolean(), server_default="false", nullable=False
        ),
    )
    op.add_column("users", sa.Column("trial_started_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("trial_expires_at", sa.DateTime(timezone=True)))
    op.add_column("users", sa.Column("trial_activation_id", sa.Uuid()))

    op.create_table(
        "trial_activations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
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
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_trial_activations_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_trial_activations")),
        sa.UniqueConstraint("user_id", name=op.f("uq_trial_activations_user_id")),
        sa.CheckConstraint(
            "expires_at > started_at",
            name=op.f("ck_trial_activations_dates_valid"),
        ),
    )
    op.create_index(
        op.f("ix_trial_activations_user_id"), "trial_activations", ["user_id"]
    )
    op.create_unique_constraint(
        op.f("uq_users_trial_activation_id"), "users", ["trial_activation_id"]
    )
    op.create_foreign_key(
        op.f("fk_users_trial_activation_id_trial_activations"),
        "users",
        "trial_activations",
        ["trial_activation_id"],
        ["id"],
        ondelete="RESTRICT",
    )

    op.create_table(
        "promo_codes",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column(
            "discount_type",
            postgresql.ENUM(
                *PROMO_TYPES, name="promo_discount_type", create_type=False
            ),
            nullable=False,
        ),
        sa.Column("discount_value", sa.Numeric(10, 2), nullable=False),
        sa.Column("bonus_days", sa.Integer()),
        sa.Column("max_uses", sa.Integer()),
        sa.Column("uses_count", sa.Integer(), server_default="0", nullable=False),
        sa.Column("per_user_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column("minimum_order_amount", sa.Numeric(10, 2)),
        sa.Column("valid_from", sa.DateTime(timezone=True)),
        sa.Column("valid_until", sa.DateTime(timezone=True)),
        sa.Column("is_active", sa.Boolean(), server_default="true", nullable=False),
        sa.Column("created_by_admin_id", sa.Integer(), nullable=False),
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
            "code = upper(btrim(code))", name=op.f("ck_promo_codes_code_normalized")
        ),
        sa.CheckConstraint(
            "discount_value > 0", name=op.f("ck_promo_codes_value_positive")
        ),
        sa.CheckConstraint(
            "discount_type != 'percent' OR discount_value <= 100",
            name=op.f("ck_promo_codes_percent_not_over_100"),
        ),
        sa.CheckConstraint(
            "bonus_days IS NULL OR bonus_days > 0",
            name=op.f("ck_promo_codes_bonus_positive"),
        ),
        sa.CheckConstraint(
            "discount_type != 'bonus_days' OR bonus_days IS NOT NULL",
            name=op.f("ck_promo_codes_bonus_required_for_type"),
        ),
        sa.CheckConstraint(
            "max_uses IS NULL OR max_uses > 0",
            name=op.f("ck_promo_codes_max_uses_positive"),
        ),
        sa.CheckConstraint(
            "uses_count >= 0", name=op.f("ck_promo_codes_uses_nonnegative")
        ),
        sa.CheckConstraint(
            "max_uses IS NULL OR uses_count <= max_uses",
            name=op.f("ck_promo_codes_uses_within_limit"),
        ),
        sa.CheckConstraint(
            "per_user_limit > 0",
            name=op.f("ck_promo_codes_per_user_limit_positive"),
        ),
        sa.CheckConstraint(
            "minimum_order_amount IS NULL OR minimum_order_amount >= 0",
            name=op.f("ck_promo_codes_minimum_amount_nonnegative"),
        ),
        sa.CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name=op.f("ck_promo_codes_validity_dates_valid"),
        ),
        sa.ForeignKeyConstraint(
            ["created_by_admin_id"],
            ["users.id"],
            name=op.f("fk_promo_codes_created_by_admin_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promo_codes")),
        sa.UniqueConstraint("code", name=op.f("uq_promo_codes_code")),
    )
    op.create_index(op.f("ix_promo_codes_code"), "promo_codes", ["code"])
    op.create_index(
        op.f("ix_promo_codes_created_by_admin_id"),
        "promo_codes",
        ["created_by_admin_id"],
    )
    op.create_index(op.f("ix_promo_codes_is_active"), "promo_codes", ["is_active"])
    op.create_index(op.f("ix_promo_codes_valid_until"), "promo_codes", ["valid_until"])

    op.create_table(
        "promo_code_tariffs",
        sa.Column("promo_code_id", sa.Uuid(), nullable=False),
        sa.Column("tariff_id", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["promo_code_id"],
            ["promo_codes.id"],
            name=op.f("fk_promo_code_tariffs_promo_code_id_promo_codes"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["tariff_id"],
            ["tariffs.id"],
            name=op.f("fk_promo_code_tariffs_tariff_id_tariffs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "promo_code_id", "tariff_id", name=op.f("pk_promo_code_tariffs")
        ),
    )

    op.add_column("orders", sa.Column("promo_code_id", sa.Uuid()))
    op.add_column("orders", sa.Column("original_amount", sa.Numeric(10, 2)))
    op.add_column(
        "orders",
        sa.Column(
            "discount_amount",
            sa.Numeric(10, 2),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column("orders", sa.Column("final_amount", sa.Numeric(10, 2)))
    op.add_column(
        "orders",
        sa.Column("bonus_days", sa.Integer(), server_default="0", nullable=False),
    )
    op.add_column("orders", sa.Column("promo_snapshot_code", sa.String(64)))
    op.add_column("orders", sa.Column("promo_snapshot_type", sa.String(20)))
    op.add_column("orders", sa.Column("promo_snapshot_value", sa.Numeric(10, 2)))
    op.execute(
        sa.text(
            "UPDATE orders SET original_amount = amount_snapshot, "
            "final_amount = amount_snapshot"
        )
    )
    op.alter_column("orders", "original_amount", nullable=False)
    op.alter_column("orders", "final_amount", nullable=False)
    op.create_foreign_key(
        op.f("fk_orders_promo_code_id_promo_codes"),
        "orders",
        "promo_codes",
        ["promo_code_id"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_index(op.f("ix_orders_promo_code_id"), "orders", ["promo_code_id"])

    op.create_table(
        "subscriptions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "source_type",
            postgresql.ENUM(
                *SUBSCRIPTION_SOURCES,
                name="subscription_source_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            postgresql.ENUM(
                *SUBSCRIPTION_STATUSES,
                name="subscription_status",
                create_type=False,
            ),
            server_default="pending",
            nullable=False,
        ),
        sa.Column("started_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("expires_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("tariff_id", sa.Integer()),
        sa.Column("order_id", sa.Uuid()),
        sa.Column("traffic_limit_gb", sa.Integer()),
        sa.Column(
            "is_unlimited_traffic",
            sa.Boolean(),
            server_default="false",
            nullable=False,
        ),
        sa.Column("device_limit", sa.Integer(), server_default="1", nullable=False),
        sa.Column("external_user_uuid", sa.String(255)),
        sa.Column("subscription_url_encrypted", sa.LargeBinary()),
        sa.Column(
            "activation_attempts", sa.Integer(), server_default="0", nullable=False
        ),
        sa.Column("last_activation_error", sa.Text()),
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
            "expires_at > started_at", name=op.f("ck_subscriptions_dates_valid")
        ),
        sa.CheckConstraint(
            "device_limit > 0",
            name=op.f("ck_subscriptions_device_limit_positive"),
        ),
        sa.CheckConstraint(
            "activation_attempts >= 0",
            name=op.f("ck_subscriptions_attempts_nonnegative"),
        ),
        sa.CheckConstraint(
            "traffic_limit_gb IS NULL OR traffic_limit_gb > 0",
            name=op.f("ck_subscriptions_traffic_limit_positive"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_subscriptions_order_id_orders"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["tariff_id"],
            ["tariffs.id"],
            name=op.f("fk_subscriptions_tariff_id_tariffs"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_subscriptions_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_subscriptions")),
        sa.UniqueConstraint("user_id", name=op.f("uq_subscriptions_user_id")),
    )
    op.create_index(
        op.f("ix_subscriptions_expires_at"), "subscriptions", ["expires_at"]
    )
    op.create_index(op.f("ix_subscriptions_status"), "subscriptions", ["status"])
    op.create_index(op.f("ix_subscriptions_user_id"), "subscriptions", ["user_id"])

    op.create_table(
        "payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("order_id", sa.Uuid(), nullable=False),
        sa.Column("provider", sa.String(32), server_default="onlipay", nullable=False),
        sa.Column("provider_payment_id", sa.String(255), nullable=False),
        sa.Column(
            "status",
            postgresql.ENUM(
                *PAYMENT_STATUSES, name="payment_status", create_type=False
            ),
            server_default="created",
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(10, 2), nullable=False),
        sa.Column("currency", sa.String(3), nullable=False),
        sa.Column("payment_url", sa.Text(), nullable=False),
        sa.Column("provider_payload_sanitized", sa.JSON()),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("webhook_received_at", sa.DateTime(timezone=True)),
        sa.Column("paid_at", sa.DateTime(timezone=True)),
        sa.Column("processed_at", sa.DateTime(timezone=True)),
        sa.Column("failure_reason", sa.Text()),
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
        sa.CheckConstraint("amount >= 0", name=op.f("ck_payments_amount_nonnegative")),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_payments_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_payments")),
        sa.UniqueConstraint(
            "idempotency_key", name=op.f("uq_payments_idempotency_key")
        ),
        sa.UniqueConstraint(
            "provider_payment_id", name=op.f("uq_payments_provider_payment_id")
        ),
    )
    op.create_index(op.f("ix_payments_order_id"), "payments", ["order_id"])
    op.create_index(
        op.f("ix_payments_provider_payment_id"),
        "payments",
        ["provider_payment_id"],
    )
    op.create_index(op.f("ix_payments_status"), "payments", ["status"])
    op.create_index(
        "uq_payments_active_order",
        "payments",
        ["order_id"],
        unique=True,
        postgresql_where=sa.text("status IN ('created', 'pending')"),
    )

    op.create_table(
        "promo_code_usages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("promo_code_id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("order_id", sa.Uuid()),
        sa.Column(
            "discount_amount",
            sa.Numeric(10, 2),
            server_default="0",
            nullable=False,
        ),
        sa.Column("bonus_days", sa.Integer(), server_default="0", nullable=False),
        sa.Column(
            "used_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "discount_amount >= 0",
            name=op.f("ck_promo_code_usages_discount_nonnegative"),
        ),
        sa.CheckConstraint(
            "bonus_days >= 0",
            name=op.f("ck_promo_code_usages_bonus_days_nonnegative"),
        ),
        sa.ForeignKeyConstraint(
            ["order_id"],
            ["orders.id"],
            name=op.f("fk_promo_code_usages_order_id_orders"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["promo_code_id"],
            ["promo_codes.id"],
            name=op.f("fk_promo_code_usages_promo_code_id_promo_codes"),
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name=op.f("fk_promo_code_usages_user_id_users"),
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_promo_code_usages")),
        sa.UniqueConstraint("order_id", name="uq_promo_code_usages_order_id"),
    )
    op.create_index(
        op.f("ix_promo_code_usages_promo_code_id"),
        "promo_code_usages",
        ["promo_code_id"],
    )
    op.create_index(
        op.f("ix_promo_code_usages_used_at"), "promo_code_usages", ["used_at"]
    )
    op.create_index(
        op.f("ix_promo_code_usages_user_id"), "promo_code_usages", ["user_id"]
    )

    op.create_table(
        "audit_logs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("actor_user_id", sa.Integer()),
        sa.Column("actor_telegram_id", sa.BigInteger()),
        sa.Column("action", sa.String(100), nullable=False),
        sa.Column("entity_type", sa.String(100), nullable=False),
        sa.Column("entity_id", sa.String(255)),
        sa.Column(
            "details_sanitized",
            sa.JSON(),
            server_default=sa.text("'{}'::json"),
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["actor_user_id"],
            ["users.id"],
            name=op.f("fk_audit_logs_actor_user_id_users"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_audit_logs")),
    )
    op.create_index(op.f("ix_audit_logs_action"), "audit_logs", ["action"])
    op.create_index(
        op.f("ix_audit_logs_actor_user_id"), "audit_logs", ["actor_user_id"]
    )
    op.create_index(op.f("ix_audit_logs_created_at"), "audit_logs", ["created_at"])


def downgrade() -> None:
    op.drop_index(op.f("ix_audit_logs_created_at"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_actor_user_id"), table_name="audit_logs")
    op.drop_index(op.f("ix_audit_logs_action"), table_name="audit_logs")
    op.drop_table("audit_logs")

    op.drop_index(op.f("ix_promo_code_usages_user_id"), table_name="promo_code_usages")
    op.drop_index(op.f("ix_promo_code_usages_used_at"), table_name="promo_code_usages")
    op.drop_index(
        op.f("ix_promo_code_usages_promo_code_id"),
        table_name="promo_code_usages",
    )
    op.drop_table("promo_code_usages")

    op.drop_index("uq_payments_active_order", table_name="payments")
    op.drop_index(op.f("ix_payments_status"), table_name="payments")
    op.drop_index(op.f("ix_payments_provider_payment_id"), table_name="payments")
    op.drop_index(op.f("ix_payments_order_id"), table_name="payments")
    op.drop_table("payments")

    op.drop_index(op.f("ix_subscriptions_user_id"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_status"), table_name="subscriptions")
    op.drop_index(op.f("ix_subscriptions_expires_at"), table_name="subscriptions")
    op.drop_table("subscriptions")

    op.drop_index(op.f("ix_orders_promo_code_id"), table_name="orders")
    op.drop_constraint(
        op.f("fk_orders_promo_code_id_promo_codes"), "orders", type_="foreignkey"
    )
    for column in (
        "promo_snapshot_value",
        "promo_snapshot_type",
        "promo_snapshot_code",
        "bonus_days",
        "final_amount",
        "discount_amount",
        "original_amount",
        "promo_code_id",
    ):
        op.drop_column("orders", column)

    op.drop_table("promo_code_tariffs")
    op.drop_index(op.f("ix_promo_codes_valid_until"), table_name="promo_codes")
    op.drop_index(op.f("ix_promo_codes_is_active"), table_name="promo_codes")
    op.drop_index(op.f("ix_promo_codes_created_by_admin_id"), table_name="promo_codes")
    op.drop_index(op.f("ix_promo_codes_code"), table_name="promo_codes")
    op.drop_table("promo_codes")

    op.drop_constraint(
        op.f("fk_users_trial_activation_id_trial_activations"),
        "users",
        type_="foreignkey",
    )
    op.drop_constraint(op.f("uq_users_trial_activation_id"), "users", type_="unique")
    op.drop_index(op.f("ix_trial_activations_user_id"), table_name="trial_activations")
    op.drop_table("trial_activations")
    for column in (
        "trial_activation_id",
        "trial_expires_at",
        "trial_started_at",
        "trial_disabled",
        "trial_used",
    ):
        op.drop_column("users", column)

    bind = op.get_bind()
    postgresql.ENUM(*PAYMENT_STATUSES, name="payment_status").drop(
        bind, checkfirst=True
    )
    postgresql.ENUM(*PROMO_TYPES, name="promo_discount_type").drop(
        bind, checkfirst=True
    )
    postgresql.ENUM(*SUBSCRIPTION_STATUSES, name="subscription_status").drop(
        bind, checkfirst=True
    )
    postgresql.ENUM(*SUBSCRIPTION_SOURCES, name="subscription_source_type").drop(
        bind, checkfirst=True
    )
