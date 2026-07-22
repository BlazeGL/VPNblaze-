import uuid
from datetime import datetime
from enum import StrEnum

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Enum,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class SubscriptionSource(StrEnum):
    trial = "trial"
    paid = "paid"
    promo = "promo"
    admin = "admin"


class SubscriptionStatus(StrEnum):
    pending = "pending"
    active = "active"
    expired = "expired"
    disabled = "disabled"
    activation_failed = "activation_failed"


class ProvisioningStatus(StrEnum):
    not_started = "not_started"
    pending = "pending"
    provisioning = "provisioning"
    active = "active"
    failed = "failed"
    disabled = "disabled"


class Subscription(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        UniqueConstraint("user_id", name="uq_subscriptions_user_id"),
        Index("ix_subscriptions_user_id", "user_id"),
        CheckConstraint("expires_at > started_at", name="dates_valid"),
        CheckConstraint("device_limit > 0", name="device_limit_positive"),
        CheckConstraint("activation_attempts >= 0", name="attempts_nonnegative"),
        CheckConstraint(
            "traffic_limit_gb IS NULL OR traffic_limit_gb > 0",
            name="traffic_limit_positive",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(primary_key=True, default=uuid.uuid4)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id", ondelete="RESTRICT"))
    source_type: Mapped[SubscriptionSource] = mapped_column(
        Enum(
            SubscriptionSource,
            name="subscription_source_type",
            values_callable=lambda values: [item.value for item in values],
        )
    )
    status: Mapped[SubscriptionStatus] = mapped_column(
        Enum(
            SubscriptionStatus,
            name="subscription_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        default=SubscriptionStatus.pending,
        server_default=SubscriptionStatus.pending.value,
        index=True,
    )
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    tariff_id: Mapped[int | None] = mapped_column(
        ForeignKey("tariffs.id", ondelete="SET NULL"), nullable=True
    )
    order_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("orders.id", ondelete="SET NULL"), nullable=True
    )
    traffic_limit_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_unlimited_traffic: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    device_limit: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    external_user_uuid: Mapped[str | None] = mapped_column(String(255), nullable=True)
    remnawave_user_uuid: Mapped[str | None] = mapped_column(
        String(36), nullable=True, unique=True, index=True
    )
    remnawave_username: Mapped[str | None] = mapped_column(
        String(36), nullable=True, unique=True, index=True
    )
    remnawave_short_uuid: Mapped[str | None] = mapped_column(String(64), nullable=True)
    subscription_url_encrypted: Mapped[bytes | None] = mapped_column(
        LargeBinary, nullable=True
    )
    activation_attempts: Mapped[int] = mapped_column(
        Integer, default=0, server_default="0"
    )
    last_activation_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    remnawave_status: Mapped[str | None] = mapped_column(String(20), nullable=True)
    remnawave_last_sync_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    remnawave_sync_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    remnawave_created_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    remnawave_internal_squad_uuid: Mapped[str | None] = mapped_column(
        String(36), nullable=True
    )
    provisioning_status: Mapped[ProvisioningStatus] = mapped_column(
        Enum(
            ProvisioningStatus,
            name="provisioning_status",
            values_callable=lambda values: [item.value for item in values],
        ),
        default=ProvisioningStatus.not_started,
        server_default=ProvisioningStatus.not_started.value,
        index=True,
    )
    used_traffic_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    connected_devices: Mapped[int | None] = mapped_column(Integer, nullable=True)
    next_retry_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True, index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
