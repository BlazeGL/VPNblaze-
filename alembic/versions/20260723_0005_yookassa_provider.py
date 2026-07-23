"""use yookassa as the default payment provider

Revision ID: 20260723_0005
Revises: 20260722_0004
Create Date: 2026-07-23
"""

from collections.abc import Sequence

from alembic import op

revision: str = "20260723_0005"
down_revision: str | None = "20260722_0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.alter_column("payments", "provider", server_default="yookassa")


def downgrade() -> None:
    raise RuntimeError(
        "Destructive database downgrades are disabled for BlazeVPN"
    )
    op.alter_column("payments", "provider", server_default="onlipay")
