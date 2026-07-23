import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class OrderStatus(StrEnum):
    pending = "pending"
    awaiting_payment = "awaiting_payment"
    paid = "paid"
    processing = "processing"
    completed = "completed"
    cancelled = "cancelled"
    expired = "expired"
    failed = "failed"


class OrderPurpose(StrEnum):
    subscription_purchase = "subscription_purchase"
    wallet_topup = "wallet_topup"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"), index=True
    )
    tariff_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="RESTRICT"), index=True, nullable=True
    )
    purpose: Mapped[OrderPurpose] = mapped_column(
        Enum(
            OrderPurpose,
            name="order_purpose",
            values_callable=lambda values: [item.value for item in values],
        ),
        default=OrderPurpose.subscription_purchase,
        server_default=OrderPurpose.subscription_purchase.value,
        index=True,
    )
    status: Mapped[OrderStatus] = mapped_column(
        Enum(
            OrderStatus,
            name="order_status",
            values_callable=lambda e: [x.value for x in e],
        ),
        default=OrderStatus.pending,
        server_default=OrderStatus.pending.value,
        index=True,
    )
    tariff_name_snapshot: Mapped[str] = mapped_column(String(255))
    duration_days_snapshot: Mapped[int] = mapped_column(Integer)
    traffic_limit_gb_snapshot: Mapped[int | None] = mapped_column(
        Integer, nullable=True
    )
    is_unlimited_traffic_snapshot: Mapped[bool] = mapped_column(Boolean)
    device_limit_snapshot: Mapped[int] = mapped_column(Integer)
    amount_snapshot: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency_snapshot: Mapped[str] = mapped_column(String(3))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
    expires_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    promo_code_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("promo_codes.id", ondelete="SET NULL"), nullable=True, index=True
    )
    original_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    discount_amount: Mapped[Decimal] = mapped_column(
        Numeric(10, 2), default=Decimal("0.00"), server_default="0"
    )
    final_amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    bonus_days: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    promo_snapshot_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    promo_snapshot_type: Mapped[str | None] = mapped_column(String(20), nullable=True)
    promo_snapshot_value: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 2), nullable=True
    )
