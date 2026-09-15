import uuid
from datetime import datetime

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class ChannelReward(Base):
    __tablename__ = "channel_rewards"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "campaign",
            name="uq_channel_rewards_user_campaign",
        ),
        CheckConstraint("bonus_days > 0", name="bonus_days_positive"),
        Index("ix_channel_rewards_user_id", "user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), nullable=False
    )
    campaign: Mapped[str] = mapped_column(String(100), nullable=False)
    bonus_days: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
