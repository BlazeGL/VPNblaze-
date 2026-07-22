from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Order,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    TrialActivation,
    User,
)


@dataclass(frozen=True)
class ProvisioningResult:
    status: SubscriptionStatus
    external_user_uuid: str | None = None
    subscription_url_encrypted: bytes | None = None


class SubscriptionAdapter(Protocol):
    async def provision(
        self, subscription: Subscription, user: User
    ) -> ProvisioningResult: ...


class DeferredSubscriptionAdapter:
    """Safe adapter used until a documented Remnawave integration is supplied."""

    async def provision(
        self, subscription: Subscription, user: User
    ) -> ProvisioningResult:
        return ProvisioningResult(status=SubscriptionStatus.pending)


class UnavailableSubscriptionAdapter:
    def __init__(self, reason: str) -> None:
        self.reason = reason

    async def provision(
        self, subscription: Subscription, user: User
    ) -> ProvisioningResult:
        raise RuntimeError(self.reason)


class SubscriptionService:
    def __init__(
        self,
        session: AsyncSession,
        adapter: SubscriptionAdapter | None = None,
    ) -> None:
        self.session = session
        self.adapter = adapter or DeferredSubscriptionAdapter()

    async def get_for_update(self, user_id: int) -> Subscription | None:
        return await self.session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .with_for_update()
        )

    async def register_trial(
        self,
        user: User,
        activation: TrialActivation,
    ) -> Subscription:
        subscription = await self.get_for_update(user.id)
        if subscription is None:
            subscription = Subscription(
                user_id=user.id,
                source_type=SubscriptionSource.trial,
                status=SubscriptionStatus.pending,
                started_at=activation.started_at,
                expires_at=activation.expires_at,
                traffic_limit_gb=None,
                is_unlimited_traffic=False,
                device_limit=1,
            )
            self.session.add(subscription)
        else:
            subscription.source_type = SubscriptionSource.trial
            subscription.status = SubscriptionStatus.pending
            subscription.started_at = activation.started_at
            subscription.expires_at = activation.expires_at
            subscription.tariff_id = None
            subscription.order_id = None
        await self.session.flush()
        return await self._provision(subscription, user)

    async def extend_from_paid_order(
        self,
        user: User,
        order: Order,
        *,
        now: datetime | None = None,
    ) -> Subscription:
        moment = now or datetime.now(UTC)
        duration_days = order.duration_days_snapshot + order.bonus_days
        subscription = await self.get_for_update(user.id)
        order_check = getattr(self.adapter, "order_was_applied", None)
        if subscription is not None and order_check is not None:
            if await order_check(order.id):
                return subscription
        if subscription is not None and subscription.order_id == order.id:
            return await self._provision(subscription, user)
        if subscription is None:
            subscription = Subscription(
                user_id=user.id,
                source_type=SubscriptionSource.paid,
                status=SubscriptionStatus.pending,
                started_at=moment,
                expires_at=moment + timedelta(days=duration_days),
                tariff_id=order.tariff_id,
                order_id=order.id,
                traffic_limit_gb=order.traffic_limit_gb_snapshot,
                is_unlimited_traffic=order.is_unlimited_traffic_snapshot,
                device_limit=order.device_limit_snapshot,
            )
            self.session.add(subscription)
        else:
            base = (
                subscription.expires_at if subscription.expires_at > moment else moment
            )
            if subscription.expires_at <= moment:
                subscription.started_at = moment
            subscription.expires_at = base + timedelta(days=duration_days)
            subscription.source_type = SubscriptionSource.paid
            subscription.status = SubscriptionStatus.pending
            subscription.tariff_id = order.tariff_id
            subscription.order_id = order.id
            subscription.traffic_limit_gb = order.traffic_limit_gb_snapshot
            subscription.is_unlimited_traffic = order.is_unlimited_traffic_snapshot
            subscription.device_limit = order.device_limit_snapshot
        await self.session.flush()
        return await self._provision(subscription, user)

    async def _provision(self, subscription: Subscription, user: User) -> Subscription:
        try:
            result = await self.adapter.provision(subscription, user)
        except Exception as exc:
            subscription.activation_attempts += 1
            subscription.status = SubscriptionStatus.activation_failed
            subscription.last_activation_error = str(exc)[:2000]
            await self.session.flush()
            return subscription
        subscription.status = result.status
        if result.external_user_uuid is not None:
            subscription.external_user_uuid = result.external_user_uuid
        if result.subscription_url_encrypted is not None:
            subscription.subscription_url_encrypted = result.subscription_url_encrypted
        if result.status != SubscriptionStatus.activation_failed:
            subscription.last_activation_error = None
        await self.session.flush()
        return subscription
