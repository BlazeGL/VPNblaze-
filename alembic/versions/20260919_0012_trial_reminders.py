"""add gradual trial reminder state

Revision ID: 20260919_0012
Revises: 20260915_0011
Create Date: 2026-09-19
"""

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "20260919_0012"
down_revision: str | None = "20260915_0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column(
        "subscriptions",
        sa.Column("trial_connection_notice_at", sa.DateTime(timezone=True)),
    )
    op.create_index(
        "ix_subscriptions_trial_connection_notice_at",
        "subscriptions",
        ["trial_connection_notice_at"],
    )

    # Do not send a connection reminder to every historical trial on deploy.
    # New trials remain NULL and enter the gradual reminder queue normally.
    op.execute(
        """
        UPDATE subscriptions
        SET trial_connection_notice_at = now()
        WHERE source_type = 'trial'
          AND trial_connection_notice_at IS NULL
        """
    )


def downgrade() -> None:
    raise RuntimeError("Destructive database downgrades are disabled for BlazeVPN")
