from collections.abc import Mapping
from dataclasses import dataclass, field
from decimal import Decimal

from app.integrations.payments import (
    CreatedPayment,
    CreatePaymentCommand,
    NormalizedPaymentStatus,
    PaymentStatusResult,
)

__all__ = [
    "CreatedPayment",
    "CreatePaymentCommand",
    "NormalizedPaymentStatus",
    "PaymentStatusResult",
    "VerifiedWebhookEvent",
]


@dataclass(frozen=True)
class VerifiedWebhookEvent:
    provider_payment_id: str
    status: NormalizedPaymentStatus
    amount: Decimal
    currency: str
    order_id: str
    merchant_id: str
    sanitized_payload: dict[str, object] = field(default_factory=dict)


Headers = Mapping[str, str]
