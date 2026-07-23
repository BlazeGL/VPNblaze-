"""bind trial history permanently to Telegram ID

Revision ID: 20260723_0008
Revises: 20260723_0007
Create Date: 2026-07-23
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260723_0008"
down_revision: str | None = "20260723_0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "trial_activations",
        sa.Column("telegram_id", sa.BigInteger(), nullable=True),
    )
    op.execute(
        """
        UPDATE trial_activations AS trial
        SET telegram_id = users.telegram_id
        FROM users
        WHERE users.id = trial.user_id
          AND trial.telegram_id IS NULL
        """
    )
    op.alter_column("trial_activations", "telegram_id", nullable=False)
    op.create_unique_constraint(
        "uq_trial_activations_telegram_id",
        "trial_activations",
        ["telegram_id"],
    )
    op.create_index(
        "ix_trial_activations_telegram_id",
        "trial_activations",
        ["telegram_id"],
    )


def downgrade() -> None:
    raise RuntimeError(
        "Destructive database downgrades are disabled for BlazeVPN"
    )
