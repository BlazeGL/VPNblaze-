import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class TrialActivation(Base):
    __tablename__ = "trial_activations"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_trial_activations_user_id"),
        UniqueConstraint(
            "telegram_id", name="uq_trial_activations_telegram_id"
        ),
        Index("ix_trial_activations_user_id", "user_id"),
        Index("ix_trial_activations_telegram_id", "telegram_id"),
        CheckConstraint("expires_at > started_at", name="dates_valid"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT")
    )
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
