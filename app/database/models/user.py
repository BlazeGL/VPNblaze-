import uuid
from datetime import datetime
from decimal import Decimal
from secrets import token_urlsafe

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    event,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column
from sqlalchemy.orm.attributes import NEVER_SET, NO_VALUE

from app.database.base import Base


def generate_referral_code() -> str:
    return token_urlsafe(12)


class User(Base):
    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint("telegram_id", name="uq_users_telegram_id"),
        UniqueConstraint("referral_code", name="uq_users_referral_code"),
        CheckConstraint("balance >= 0", name="balance_nonnegative"),
        CheckConstraint("total_referrals >= 0", name="total_referrals_nonnegative"),
        CheckConstraint(
            "total_referral_income >= 0",
            name="total_referral_income_nonnegative",
        ),
        CheckConstraint(
            "referred_by IS NULL OR referred_by != id",
            name="no_self_referral",
        ),
        Index("ix_users_telegram_id", "telegram_id"),
        Index("ix_users_referral_code", "referral_code"),
        Index("ix_users_referred_by", "referred_by"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    telegram_id: Mapped[int] = mapped_column(BigInteger)
    username: Mapped[str | None] = mapped_column(String(255), nullable=True)
    first_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    last_name: Mapped[str | None] = mapped_column(String(255), nullable=True)
    balance: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0.00"
    )
    referral_code: Mapped[str] = mapped_column(
        String(32), default=generate_referral_code
    )
    referred_by: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    total_referrals: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    total_referral_income: Mapped[Decimal] = mapped_column(
        Numeric(12, 2), default=Decimal("0.00"), server_default="0.00"
    )
    is_admin: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    is_blocked: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    trial_used: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    trial_disabled: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    trial_started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    trial_activation_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(
            "trial_activations.id",
            ondelete="RESTRICT",
            use_alter=True,
            name="fk_users_trial_activation_id_trial_activations",
        ),
        unique=True,
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    last_activity_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


@event.listens_for(
    User.referral_code, "set", retval=True, active_history=True
)
def keep_referral_code_immutable(
    _target: User, value: str, old_value: object, _initiator: object
) -> str:
    if old_value not in {NEVER_SET, NO_VALUE, None} and old_value != value:
        raise ValueError("referral_code is immutable")
    return value
