import uuid
from datetime import datetime
from decimal import Decimal
from enum import StrEnum

from sqlalchemy import (
    JSON,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class PaymentStatus(StrEnum):
    created = "created"
    pending = "pending"
    paid = "paid"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"
    refunded = "refunded"


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        UniqueConstraint(
            "provider_payment_id", name="uq_payments_provider_payment_id"
        ),
        Index("ix_payments_provider_payment_id", "provider_payment_id"),
        CheckConstraint("amount >= 0", name="amount_nonnegative"),
        Index(
            "uq_payments_active_order",
            "order_id",
            unique=True,
            postgresql_where=text("status IN ('created', 'pending')"),
            sqlite_where=text("status IN ('created', 'pending')"),
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    order_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("orders.id", ondelete="RESTRICT"), index=True
    )
    provider: Mapped[str] = mapped_column(
        String(32), default="onlipay", server_default="onlipay"
    )
    provider_payment_id: Mapped[str] = mapped_column(String(255))
    status: Mapped[PaymentStatus] = mapped_column(
        Enum(
            PaymentStatus,
            name="payment_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        default=PaymentStatus.created,
        server_default=PaymentStatus.created.value,
        index=True,
    )
    amount: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    currency: Mapped[str] = mapped_column(String(3))
    payment_url: Mapped[str] = mapped_column(Text)
    provider_payload_sanitized: Mapped[dict[str, object] | None] = mapped_column(
        JSON, nullable=True
    )
    idempotency_key: Mapped[str] = mapped_column(String(255), unique=True)
    webhook_received_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    paid_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    processed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    failure_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
