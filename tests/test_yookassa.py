import base64
import json
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.integrations.payments import CreatePaymentCommand, NormalizedPaymentStatus
from app.integrations.yookassa.client import YooKassaClient
from app.integrations.yookassa.exceptions import (
    YooKassaConfigurationError,
    YooKassaRequestError,
)
from app.webhooks.yookassa import router as webhook_router


@pytest.mark.asyncio
async def test_create_yookassa_redirect_payment() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/v3/payments"
        assert request.headers["Idempotence-Key"] == "idem-1"
        expected_auth = base64.b64encode(b"1234567:secret").decode()
        assert request.headers["Authorization"] == f"Basic {expected_auth}"
        body = json.loads(request.content)
        assert body["amount"] == {"value": "499.00", "currency": "RUB"}
        assert body["capture"] is True
        assert body["confirmation"] == {
            "type": "redirect",
            "return_url": "https://t.me/example_bot",
        }
        assert body["metadata"] == {"order_id": "order-1"}
        return httpx.Response(
            200,
            json={
                "id": "payment-1",
                "status": "pending",
                "paid": False,
                "amount": {"value": "499.00", "currency": "RUB"},
                "confirmation": {
                    "type": "redirect",
                    "confirmation_url": "https://yoomoney.ru/checkout/payment-1",
                },
                "metadata": {"order_id": "order-1"},
                "test": True,
            },
        )

    client = YooKassaClient(
        "1234567",
        "secret",
        default_return_url="https://t.me/example_bot",
        transport=httpx.MockTransport(handler),
    )
    try:
        result = await client.create_payment(
            CreatePaymentCommand(
                order_id="order-1",
                amount=Decimal("499"),
                currency="rub",
                idempotency_key="idem-1",
            )
        )
    finally:
        await client.aclose()

    assert result.provider_payment_id == "payment-1"
    assert result.status == NormalizedPaymentStatus.pending
    assert result.payment_url == "https://yoomoney.ru/checkout/payment-1"
    assert "confirmation" not in result.sanitized_payload


@pytest.mark.asyncio
async def test_get_yookassa_payment_maps_verified_status() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/v3/payments/payment-1"
        return httpx.Response(
            200,
            json={
                "id": "payment-1",
                "status": "succeeded",
                "paid": True,
                "amount": {"value": "499.00", "currency": "RUB"},
                "metadata": {"order_id": "order-1"},
            },
        )

    client = YooKassaClient(
        "1234567", "secret", transport=httpx.MockTransport(handler)
    )
    try:
        result = await client.get_payment_status("payment-1")
    finally:
        await client.aclose()

    assert result.status == NormalizedPaymentStatus.paid
    assert result.amount == Decimal("499.00")
    assert result.order_id == "order-1"


@pytest.mark.asyncio
async def test_yookassa_client_fails_closed_without_credentials() -> None:
    client = YooKassaClient(None, None)
    with pytest.raises(YooKassaConfigurationError):
        await client.check_api()


def test_yookassa_webhook_verifies_with_api_before_database() -> None:
    class SessionFactory:
        calls = 0

        def __call__(self) -> object:
            self.calls += 1
            raise AssertionError("DB must not be opened before API verification")

    app = FastAPI()
    app.include_router(webhook_router)
    factory = SessionFactory()
    api_client = SimpleNamespace(
        get_payment_status=AsyncMock(
            side_effect=YooKassaRequestError("network", retryable=True)
        )
    )
    app.state.settings = SimpleNamespace()
    app.state.yookassa_client = api_client
    app.state.session_factory = factory

    response = TestClient(app).post(
        "/api/webhooks/yookassa",
        json={
            "type": "notification",
            "event": "payment.succeeded",
            "object": {"id": "payment-1"},
        },
    )

    assert response.status_code == 502
    assert factory.calls == 0
    api_client.get_payment_status.assert_awaited_once_with("payment-1")


def test_yookassa_webhook_rejects_invalid_payload_before_api() -> None:
    app = FastAPI()
    app.include_router(webhook_router)
    api_client = SimpleNamespace(get_payment_status=AsyncMock())
    app.state.settings = SimpleNamespace()
    app.state.yookassa_client = api_client
    app.state.session_factory = SimpleNamespace()

    response = TestClient(app).post(
        "/api/webhooks/yookassa",
        json={"type": "notification", "event": "payment.succeeded", "object": {}},
    )

    assert response.status_code == 400
    api_client.get_payment_status.assert_not_awaited()
