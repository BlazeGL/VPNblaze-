"""add one-time channel subscription rewards

Revision ID: 20260915_0011
Revises: 20260803_0010
Create Date: 2026-09-15
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260915_0011"
down_revision: str | None = "20260803_0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "channel_rewards",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("user_id", sa.Integer(), nullable=False),
        sa.Column("campaign", sa.String(length=100), nullable=False),
        sa.Column("bonus_days", sa.Integer(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.CheckConstraint(
            "bonus_days > 0",
            name="ck_channel_rewards_bonus_days_positive",
        ),
        sa.ForeignKeyConstraint(
            ["user_id"],
            ["users.id"],
            name="fk_channel_rewards_user_id_users",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id", name="pk_channel_rewards"),
        sa.UniqueConstraint(
            "user_id",
            "campaign",
            name="uq_channel_rewards_user_campaign",
        ),
    )
    op.create_index(
        "ix_channel_rewards_user_id",
        "channel_rewards",
        ["user_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive database downgrades are disabled for BlazeVPN"
    )
