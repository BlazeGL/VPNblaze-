from typing import Protocol

from app.integrations.onlipay.exceptions import OnliPayUnavailableError
from app.integrations.onlipay.schemas import Headers, VerifiedWebhookEvent


class OnliPayWebhookVerifier(Protocol):
    """Implemented only from the merchant's official signature specification."""

    def verify_and_decode(
        self, body: bytes, headers: Headers
    ) -> VerifiedWebhookEvent: ...


class UnavailableWebhookVerifier:
    def verify_and_decode(self, body: bytes, headers: Headers) -> VerifiedWebhookEvent:
        raise OnliPayUnavailableError(
            "OnliPay webhook signature contract is not configured"
        )
