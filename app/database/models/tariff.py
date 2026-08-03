from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    Numeric,
    String,
    Text,
    func,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.database.base import Base


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (
        CheckConstraint("duration_days > 0", name="duration_positive"),
        CheckConstraint("price > 0", name="price_positive"),
        CheckConstraint("device_limit > 0", name="device_limit_positive"),
        CheckConstraint(
            "traffic_limit_gb > 0 OR "
            "(is_unlimited_traffic AND traffic_limit_gb IS NULL)",
            name="traffic_limit_valid",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(255))
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    duration_days: Mapped[int] = mapped_column(Integer)
    price: Mapped[Decimal] = mapped_column(Numeric(10, 2))
    show_price_in_button: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        server_default="true",
    )
    currency: Mapped[str] = mapped_column(
        String(3), default="RUB", server_default="RUB"
    )
    traffic_limit_gb: Mapped[int | None] = mapped_column(Integer, nullable=True)
    is_unlimited_traffic: Mapped[bool] = mapped_column(
        Boolean, default=False, server_default="false"
    )
    device_limit: Mapped[int] = mapped_column(Integer)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, server_default="true", index=True
    )
    sort_order: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )
