from typing import Protocol

from app.integrations.onlipay.exceptions import OnliPayUnavailableError
from app.integrations.onlipay.schemas import (
    CreatedPayment,
    CreatePaymentCommand,
    PaymentStatusResult,
)


class OnliPayTransport(Protocol):
    """Maps the private official OnliPay contract to normalized domain schemas."""

    async def create_payment(self, command: CreatePaymentCommand) -> CreatedPayment: ...

    async def get_payment_status(
        self, provider_payment_id: str
    ) -> PaymentStatusResult: ...


class OnliPayClient:
    """Fail-closed facade until OnliPay supplies its merchant API documentation."""

    provider_name = "onlipay"

    def __init__(self, transport: OnliPayTransport | None = None) -> None:
        self.transport = transport

    @property
    def is_configured(self) -> bool:
        return self.transport is not None

    async def create_payment(self, command: CreatePaymentCommand) -> CreatedPayment:
        if self.transport is None:
            raise OnliPayUnavailableError(
                "OnliPay merchant API contract is not configured"
            )
        return await self.transport.create_payment(command)

    async def get_payment_status(self, provider_payment_id: str) -> PaymentStatusResult:
        if self.transport is None:
            raise OnliPayUnavailableError(
                "OnliPay merchant API contract is not configured"
            )
        return await self.transport.get_payment_status(provider_payment_id)
