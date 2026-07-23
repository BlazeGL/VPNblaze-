from dataclasses import dataclass, field
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class NormalizedPaymentStatus(StrEnum):
    created = "created"
    pending = "pending"
    paid = "paid"
    failed = "failed"
    cancelled = "cancelled"
    expired = "expired"
    refunded = "refunded"
    unknown = "unknown"


@dataclass(frozen=True)
class CreatePaymentCommand:
    order_id: str
    amount: Decimal
    currency: str
    idempotency_key: str
    return_url: str | None = None
    webhook_url: str | None = None


@dataclass(frozen=True)
class CreatedPayment:
    provider_payment_id: str
    payment_url: str
    status: NormalizedPaymentStatus
    sanitized_payload: dict[str, object] = field(default_factory=dict)


@dataclass(frozen=True)
class PaymentStatusResult:
    provider_payment_id: str
    status: NormalizedPaymentStatus
    amount: Decimal
    currency: str
    order_id: str
    sanitized_payload: dict[str, object] = field(default_factory=dict)


class PaymentProviderClient(Protocol):
    provider_name: str

    async def create_payment(self, command: CreatePaymentCommand) -> CreatedPayment: ...

    async def get_payment_status(
        self, provider_payment_id: str
    ) -> PaymentStatusResult: ...
