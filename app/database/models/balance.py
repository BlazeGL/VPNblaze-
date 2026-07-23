import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class BalanceTransactionType(StrEnum):
    topup = "topup"
    referral_bonus = "referral_bonus"
    daily_charge = "daily_charge"
    refund = "refund"
    adjustment = "adjustment"


class BalanceTransaction(Base):
    __tablename__ = "balance_transactions"
    __table_args__ = (
        UniqueConstraint(
            "idempotency_key", name="uq_balance_transactions_idempotency_key"
        ),
        CheckConstraint("balance_before >= 0", name="balance_before_nonnegative"),
        CheckConstraint("balance_after >= 0", name="balance_after_nonnegative"),
        CheckConstraint("amount != 0", name="amount_nonzero"),
        Index("ix_balance_transactions_user_id", "user_id"),
        Index("ix_balance_transactions_type", "type"),
        Index("ix_balance_transactions_created_at", "created_at"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    type: Mapped[BalanceTransactionType] = mapped_column(
        Enum(
            BalanceTransactionType,
            name="balance_transaction_type",
            values_callable=lambda values: [item.value for item in values],
        )
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    balance_before: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    balance_after: Mapped[Decimal] = mapped_column(Numeric(12, 2))
    idempotency_key: Mapped[str] = mapped_column(String(255))
    reference_type: Mapped[str | None] = mapped_column(String(32), nullable=True)
    reference_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
