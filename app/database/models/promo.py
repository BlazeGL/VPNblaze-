import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PromoDiscountType(StrEnum):
    percent = "percent"
    fixed = "fixed"
    bonus_days = "bonus_days"


class PromoCode(Base):
    __tablename__ = "promo_codes"
    __table_args__ = (
        UniqueConstraint("code", name="uq_promo_codes_code"),
        Index("ix_promo_codes_code", "code"),
        CheckConstraint("code = upper(btrim(code))", name="code_normalized"),
        CheckConstraint("discount_value > 0", name="value_positive"),
        CheckConstraint(
            "discount_type != 'percent' OR discount_value <= 100",
            name="percent_not_over_100",
        ),
        CheckConstraint("bonus_days IS NULL OR bonus_days > 0", name="bonus_positive"),
        CheckConstraint(
            "discount_type != 'bonus_days' OR bonus_days IS NOT NULL",
            name="bonus_required_for_type",
        ),
        CheckConstraint("max_uses IS NULL OR max_uses > 0", name="max_uses_positive"),
        CheckConstraint("uses_count >= 0", name="uses_nonnegative"),
        CheckConstraint(
            "max_uses IS NULL OR uses_count <= max_uses",
            name="uses_within_limit",
        ),
        CheckConstraint("per_user_limit > 0", name="per_user_limit_positive"),
        CheckConstraint(
            "minimum_order_amount IS NULL OR minimum_order_amount >= 0",
            name="minimum_amount_nonnegative",
        ),
        CheckConstraint(
            "valid_until IS NULL OR valid_from IS NULL OR valid_until > valid_from",
            name="validity_dates_valid",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    code: Mapped[str] = mapped_column(String(64))
    discount_type: Mapped[PromoDiscountType] = mapped_column(
        Enum(
            PromoDiscountType,
            name="promo_discount_type",
            values_callable=lambda values: [item.value for item in values],
        )
    )
    discount_value: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    bonus_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    max_uses: Mapped[int | None] = mapped_column(Integer, nullable=True)
    uses_count: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    per_user_limit: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    minimum_order_amount: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
    valid_from: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    valid_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", index=True
    )
    created_by_admin_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class PromoCodeTariff(Base):
    __tablename__ = "promo_code_tariffs"

    promo_code_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="CASCADE"), primary_key=True
    )
    tariff_id: Mapped[int] = mapped_column(
        ForeignKey("tariffs.id", ondelete="CASCADE"), primary_key=True
    )


class PromoCodeUsage(Base):
    __tablename__ = "promo_code_usages"
    __table_args__ = (
        UniqueConstraint("order_id", name="uq_promo_code_usages_order_id"),
        CheckConstraint("discount_amount >= 0", name="discount_nonnegative"),
        CheckConstraint("bonus_days >= 0", name="bonus_days_nonnegative"),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    promo_code_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="RESTRICT"), index=True
    )
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), nullable=True
    )
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00"), server_default="0"
    )
    bonus_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    used_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), index=True
    )
