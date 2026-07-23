import logging
from collections.abc import Mapping
from decimal import Decimal, InvalidOperation
from urllib.parse import quote

import httpx

from app.integrations.payments import (
    CreatedPayment,
    CreatePaymentCommand,
    NormalizedPaymentStatus,
    PaymentStatusResult,
)
from app.integrations.yookassa.exceptions import (
    YooKassaConfigurationError,
    YooKassaRequestError,
    YooKassaResponseError,
)

logger = logging.getLogger(__name__)

STATUS_MAP = {
    "pending": NormalizedPaymentStatus.pending,
    "waiting_for_capture": NormalizedPaymentStatus.pending,
    "succeeded": NormalizedPaymentStatus.paid,
    "canceled": NormalizedPaymentStatus.cancelled,
}


class YooKassaClient:
    """Async client for the documented YooKassa Payments API v3."""

    provider_name = "yookassa"

    def __init__(
        self,
        shop_id: str | None,
        secret_key: str | None,
        *,
        base_url: str = "https://api.yookassa.ru/v3",
        default_return_url: str | None = None,
        timeout: float = 15,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self.shop_id = shop_id.strip() if shop_id else None
        self._secret_key = secret_key.strip() if secret_key else None
        self.default_return_url = (
            default_return_url.strip() if default_return_url else None
        )
        self._client: httpx.AsyncClient | None = None
        if self.shop_id and self._secret_key:
            self._client = httpx.AsyncClient(
                base_url=base_url.rstrip("/"),
                auth=httpx.BasicAuth(self.shop_id, self._secret_key),
                headers={
                    "Accept": "application/json",
                    "Content-Type": "application/json",
                    "User-Agent": "BlazeVPN/1.0 YooKassa",
                },
                timeout=httpx.Timeout(timeout),
                transport=transport,
            )

    @property
    def is_configured(self) -> bool:
        return self._client is not None

    async def __aenter__(self) -> "YooKassaClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()

    async def check_api(self) -> bool:
        await self._request("GET", "/payments", params={"limit": 1})
        return True

    async def create_payment(self, command: CreatePaymentCommand) -> CreatedPayment:
        if command.amount <= 0:
            raise YooKassaConfigurationError("Payment amount must be positive")
        return_url = command.return_url or self.default_return_url
        if not return_url:
            raise YooKassaConfigurationError("YooKassa return URL is not configured")
        currency = command.currency.upper()
        if len(currency) != 3:
            raise YooKassaConfigurationError("Payment currency must be ISO 4217")

        payload = {
            "amount": {
                "value": f"{command.amount.quantize(Decimal('0.01')):.2f}",
                "currency": currency,
            },
            "capture": True,
            "confirmation": {
                "type": "redirect",
                "return_url": return_url,
            },
            "description": f"VPN order {command.order_id}"[:128],
            "metadata": {"order_id": command.order_id},
        }
        response = await self._request(
            "POST",
            "/payments",
            headers={"Idempotence-Key": command.idempotency_key},
            json=payload,
        )
        data = self._response_mapping(response)
        provider_id = self._required_string(data, "id")
        confirmation = data.get("confirmation")
        if not isinstance(confirmation, Mapping):
            raise YooKassaResponseError(
                "YooKassa response has no payment confirmation"
            )
        confirmation_url = self._required_string(confirmation, "confirmation_url")
        return CreatedPayment(
            provider_payment_id=provider_id,
            payment_url=confirmation_url,
            status=self._map_status(data.get("status")),
            sanitized_payload=self._sanitize_payment(data),
        )

    async def get_payment_status(
        self, provider_payment_id: str
    ) -> PaymentStatusResult:
        if not provider_payment_id:
            raise YooKassaConfigurationError("Payment ID is required")
        response = await self._request(
            "GET", f"/payments/{quote(provider_payment_id, safe='')}"
        )
        data = self._response_mapping(response)
        amount = data.get("amount")
        metadata = data.get("metadata")
        if not isinstance(amount, Mapping) or not isinstance(metadata, Mapping):
            raise YooKassaResponseError("YooKassa payment response is incomplete")
        try:
            value = Decimal(self._required_string(amount, "value"))
        except InvalidOperation as exc:
            raise YooKassaResponseError(
                "YooKassa returned an invalid payment amount"
            ) from exc
        return PaymentStatusResult(
            provider_payment_id=self._required_string(data, "id"),
            status=self._map_status(data.get("status")),
            amount=value,
            currency=self._required_string(amount, "currency").upper(),
            order_id=self._required_string(metadata, "order_id"),
            sanitized_payload=self._sanitize_payment(data),
        )

    async def _request(
        self, method: str, path: str, **kwargs: object
    ) -> httpx.Response:
        if self._client is None:
            raise YooKassaConfigurationError("YooKassa credentials are not configured")
        try:
            response = await self._client.request(method, path, **kwargs)
        except httpx.TransportError as exc:
            raise YooKassaRequestError(
                "YooKassa network request failed", retryable=True
            ) from exc
        if 200 <= response.status_code < 300:
            return response
        error_code = "unknown"
        try:
            body = response.json()
            if isinstance(body, Mapping) and isinstance(body.get("code"), str):
                error_code = body["code"]
        except ValueError:
            pass
        logger.error(
            "YooKassa API error method=%s path=%s status=%s code=%s",
            method,
            path,
            response.status_code,
            error_code,
        )
        raise YooKassaRequestError(
            f"YooKassa API returned HTTP {response.status_code} ({error_code})",
            status_code=response.status_code,
            retryable=response.status_code == 429 or response.status_code >= 500,
        )

    @staticmethod
    def _response_mapping(response: httpx.Response) -> Mapping[str, object]:
        try:
            data = response.json()
        except ValueError as exc:
            raise YooKassaResponseError("YooKassa returned invalid JSON") from exc
        if not isinstance(data, Mapping):
            raise YooKassaResponseError("YooKassa returned an invalid response")
        return data

    @staticmethod
    def _required_string(data: Mapping[object, object], key: str) -> str:
        value = data.get(key)
        if not isinstance(value, str) or not value.strip():
            raise YooKassaResponseError(
                f"YooKassa response is missing required field: {key}"
            )
        return value

    @staticmethod
    def _map_status(value: object) -> NormalizedPaymentStatus:
        if not isinstance(value, str):
            return NormalizedPaymentStatus.unknown
        return STATUS_MAP.get(value, NormalizedPaymentStatus.unknown)

    @classmethod
    def _sanitize_payment(cls, data: Mapping[str, object]) -> dict[str, object]:
        sanitized: dict[str, object] = {}
        for key in ("id", "status", "paid", "test", "refundable", "created_at"):
            value = data.get(key)
            if isinstance(value, (str, bool, int, float)) or value is None:
                sanitized[key] = value
        amount = data.get("amount")
        if isinstance(amount, Mapping):
            sanitized["amount"] = {
                key: value
                for key in ("value", "currency")
                if isinstance((value := amount.get(key)), str)
            }
        metadata = data.get("metadata")
        if isinstance(metadata, Mapping) and isinstance(
            metadata.get("order_id"), str
        ):
            sanitized["metadata"] = {"order_id": metadata["order_id"]}
        cancellation = data.get("cancellation_details")
        if isinstance(cancellation, Mapping):
            sanitized["cancellation_details"] = {
                key: value
                for key in ("party", "reason")
                if isinstance((value := cancellation.get(key)), str)
            }
        return sanitized
