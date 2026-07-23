import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime
from decimal import Decimal
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)
from app.services.balance import (
    BalanceService,
    InsufficientBalanceError,
    normalize_money,
)

logger = logging.getLogger(__name__)
DAILY_VPN_PRICE = Decimal("5.00")


class RemnawaveBillingClient(Protocol):
    async def disable_user(self, user_uuid: str) -> object: ...

    async def enable_user(self, user_uuid: str) -> object: ...


@dataclass(frozen=True)
class BillingResult:
    charged: bool
    disabled: bool
    already_processed: bool
    transaction: BalanceTransaction | None = None


class BillingService:
    def __init__(
        self,
        session: AsyncSession,
        remnawave_client: RemnawaveBillingClient | None = None,
    ) -> None:
        self.session = session
        self.remnawave_client = remnawave_client

    async def charge_subscription(
        self,
        subscription_id: object,
        *,
        billing_date: date | None = None,
    ) -> BillingResult:
        day = billing_date or datetime.now(UTC).date()
        candidate = await self.session.scalar(
            select(Subscription).where(Subscription.id == subscription_id)
        )
        if candidate is None:
            return BillingResult(False, False, False)
        user = await self.session.scalar(
            select(User).where(User.id == candidate.user_id).with_for_update()
        )
        if user is None:
            return BillingResult(False, False, False)
        subscription = await self.session.scalar(
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .with_for_update()
        )
        if (
            subscription is None
            or subscription.status != SubscriptionStatus.active
            or subscription.source_type == SubscriptionSource.trial
        ):
            return BillingResult(False, False, False)

        try:
            change = await BalanceService(self.session).debit(
                subscription.user_id,
                amount=DAILY_VPN_PRICE,
                transaction_type=BalanceTransactionType.daily_charge,
                idempotency_key=f"daily:{subscription.id}:{day.isoformat()}",
                reference_type="subscription",
                reference_id=str(subscription.id),
                locked_user=user,
            )
        except InsufficientBalanceError:
            await self._disable(subscription)
            return BillingResult(False, True, False)

        return BillingResult(
            charged=not change.already_applied,
            disabled=False,
            already_processed=change.already_applied,
            transaction=change.transaction,
        )

    async def reactivate(self, user_id: int) -> Subscription:
        user = await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        subscription = await self.session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .with_for_update()
        )
        if user is None or subscription is None:
            raise LookupError("subscription_not_found")
        if normalize_money(user.balance) < DAILY_VPN_PRICE:
            raise InsufficientBalanceError("insufficient_balance")
        if subscription.remnawave_user_uuid and self.remnawave_client:
            await self.remnawave_client.enable_user(
                subscription.remnawave_user_uuid
            )
        subscription.status = SubscriptionStatus.active
        subscription.provisioning_status = ProvisioningStatus.active
        subscription.remnawave_status = "ACTIVE"
        await self.session.flush()
        return subscription

    async def _disable(self, subscription: Subscription) -> None:
        if subscription.remnawave_user_uuid and self.remnawave_client:
            try:
                await self.remnawave_client.disable_user(
                    subscription.remnawave_user_uuid
                )
            except Exception:
                logger.exception(
                    "Could not disable Remnawave user for subscription %s",
                    subscription.id,
                )
        subscription.status = SubscriptionStatus.disabled
        subscription.provisioning_status = ProvisioningStatus.disabled
        subscription.remnawave_status = "DISABLED"
        await self.session.flush()
