"""add balances, referrals, and Remnawave traffic limits

Revision ID: 20260723_0006
Revises: 20260723_0005
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

from alembic import op

revision: str = "20260723_0006"
down_revision: str | None = "20260723_0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

TRANSACTION_TYPES = (
    "topup",
    "referral_bonus",
    "daily_charge",
    "refund",
    "adjustment",
)


def upgrade() -> None:
    bind = op.get_bind()
    transaction_type = postgresql.ENUM(
        *TRANSACTION_TYPES, name="balance_transaction_type"
    )
    transaction_type.create(bind, checkfirst=True)

    op.add_column(
        "users",
        sa.Column(
            "balance",
            sa.Numeric(12, 2),
            server_default="0.00",
            nullable=False,
        ),
    )
    op.add_column("users", sa.Column("referral_code", sa.String(32)))
    op.add_column("users", sa.Column("referred_by", sa.Integer()))
    op.add_column(
        "users",
        sa.Column(
            "total_referrals",
            sa.Integer(),
            server_default="0",
            nullable=False,
        ),
    )
    op.add_column(
        "users",
        sa.Column(
            "total_referral_income",
            sa.Numeric(12, 2),
            server_default="0.00",
            nullable=False,
        ),
    )
    op.execute(
        """
        UPDATE users
        SET referral_code = substr(
            md5(id::text || clock_timestamp()::text || random()::text),
            1,
            16
        )
        WHERE referral_code IS NULL
        """
    )
    op.alter_column("users", "referral_code", nullable=False)
    op.create_unique_constraint(
        "uq_users_referral_code", "users", ["referral_code"]
    )
    op.create_index("ix_users_referral_code", "users", ["referral_code"])
    op.create_index("ix_users_referred_by", "users", ["referred_by"])
    op.create_foreign_key(
        "fk_users_referred_by_users",
        "users",
        "users",
        ["referred_by"],
        ["id"],
        ondelete="SET NULL",
    )
    op.create_check_constraint(
        "balance_nonnegative", "users", "balance >= 0"
    )
    op.create_check_constraint(
        "total_referrals_nonnegative", "users", "total_referrals >= 0"
    )
    op.create_check_constraint(
        "total_referral_income_nonnegative",
        "users",
        "total_referral_income >= 0",
    )
    op.create_check_constraint(
        "no_self_referral",
        "users",
        "referred_by IS NULL OR referred_by != id",
    )

    op.create_table(
        "balance_transactions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column(
            "type",
            postgresql.ENUM(
                *TRANSACTION_TYPES,
                name="balance_transaction_type",
                create_type=False,
            ),
            nullable=False,
        ),
        sa.Column("amount", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_before", sa.Numeric(12, 2), nullable=False),
        sa.Column("balance_after", sa.Numeric(12, 2), nullable=False),
        sa.Column("idempotency_key", sa.String(255), nullable=False),
        sa.Column("reference_type", sa.String(32)),
        sa.Column("reference_id", sa.String(255)),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint("amount != 0", name="amount_nonzero"),
        sa.CheckConstraint(
            "balance_after >= 0", name="balance_after_nonnegative"
        ),
        sa.CheckConstraint(
            "balance_before >= 0", name="balance_before_nonnegative"
        ),
        sa.ForeignKeyConstraint(["user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "idempotency_key",
            name="uq_balance_transactions_idempotency_key",
        ),
    )
    op.create_index(
        "ix_balance_transactions_user_id",
        "balance_transactions",
        ["user_id"],
    )
    op.create_index(
        "ix_balance_transactions_type",
        "balance_transactions",
        ["type"],
    )
    op.create_index(
        "ix_balance_transactions_created_at",
        "balance_transactions",
        ["created_at"],
    )

    op.alter_column(
        "subscriptions",
        "used_traffic_bytes",
        existing_type=sa.Integer(),
        type_=sa.BigInteger(),
        postgresql_using="used_traffic_bytes::bigint",
    )
    op.add_column(
        "subscriptions",
        sa.Column("remnawave_traffic_limit_bytes", sa.BigInteger()),
    )


def downgrade() -> None:
    bind = op.get_bind()
    op.drop_column("subscriptions", "remnawave_traffic_limit_bytes")
    op.alter_column(
        "subscriptions",
        "used_traffic_bytes",
        existing_type=sa.BigInteger(),
        type_=sa.Integer(),
        postgresql_using="used_traffic_bytes::integer",
    )

    op.drop_index(
        "ix_balance_transactions_created_at",
        table_name="balance_transactions",
    )
    op.drop_index(
        "ix_balance_transactions_type", table_name="balance_transactions"
    )
    op.drop_index(
        "ix_balance_transactions_user_id", table_name="balance_transactions"
    )
    op.drop_table("balance_transactions")

    op.drop_constraint(
        "ck_users_total_referral_income_nonnegative",
        "users",
        type_="check",
    )
    op.drop_constraint(
        "ck_users_no_self_referral", "users", type_="check"
    )
    op.drop_constraint(
        "ck_users_total_referrals_nonnegative", "users", type_="check"
    )
    op.drop_constraint(
        "ck_users_balance_nonnegative", "users", type_="check"
    )
    op.drop_constraint(
        "fk_users_referred_by_users", "users", type_="foreignkey"
    )
    op.drop_index("ix_users_referred_by", table_name="users")
    op.drop_index("ix_users_referral_code", table_name="users")
    op.drop_constraint("uq_users_referral_code", "users", type_="unique")
    for column in (
        "total_referral_income",
        "total_referrals",
        "referred_by",
        "referral_code",
        "balance",
    ):
        op.drop_column("users", column)

    postgresql.ENUM(
        *TRANSACTION_TYPES, name="balance_transaction_type"
    ).drop(bind, checkfirst=True)
