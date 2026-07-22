from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    TrialActivation,
    User,
)
from app.services.audit import add_audit_log
from app.services.subscriptions import SubscriptionService


@dataclass(frozen=True)
class TrialActivationResult:
    activated: bool
    reason: str
    activation: TrialActivation | None = None
    subscription: Subscription | None = None


class TrialService:
    DAYS = 7

    def __init__(
        self,
        session: AsyncSession,
        subscription_service: SubscriptionService | None = None,
    ) -> None:
        self.session = session
        self.subscription_service = subscription_service or SubscriptionService(session)

    async def activate(
        self,
        telegram_id: int,
        *,
        now: datetime | None = None,
    ) -> TrialActivationResult:
        moment = now or datetime.now(UTC)
        user = await self.session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            return TrialActivationResult(False, "user_not_found")

        existing = await self.session.scalar(
            select(TrialActivation).where(TrialActivation.user_id == user.id)
        )
        if user.trial_used or existing is not None:
            add_audit_log(
                self.session,
                action="trial_repeated_attempt",
                entity_type="user",
                entity_id=user.id,
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_id,
            )
            await self.session.flush()
            return TrialActivationResult(False, "already_used", existing)
        if user.trial_disabled:
            return TrialActivationResult(False, "disabled")
        if user.is_blocked:
            return TrialActivationResult(False, "blocked")

        paid_subscription = await self.session.scalar(
            select(Subscription).where(
                Subscription.user_id == user.id,
                Subscription.source_type == SubscriptionSource.paid,
                Subscription.status.in_(
                    [SubscriptionStatus.active, SubscriptionStatus.pending]
                ),
                Subscription.expires_at > moment,
            )
        )
        if paid_subscription is not None:
            return TrialActivationResult(False, "paid_subscription_exists")

        activation = TrialActivation(
            user_id=user.id,
            started_at=moment,
            expires_at=moment + timedelta(days=self.DAYS),
        )
        self.session.add(activation)
        await self.session.flush()
        user.trial_used = True
        user.trial_started_at = activation.started_at
        user.trial_expires_at = activation.expires_at
        user.trial_activation_id = activation.id
        subscription = await self.subscription_service.register_trial(user, activation)
        add_audit_log(
            self.session,
            action="trial_activated",
            entity_type="trial_activation",
            entity_id=activation.id,
            actor_user_id=user.id,
            actor_telegram_id=user.telegram_id,
            details={"expires_at": activation.expires_at.isoformat()},
        )
        await self.session.flush()
        return TrialActivationResult(True, "activated", activation, subscription)

    async def set_disabled(
        self,
        telegram_id: int,
        *,
        disabled: bool,
        actor_user_id: int | None,
        actor_telegram_id: int,
    ) -> User:
        user = await self.session.scalar(
            select(User)
            .where(User.telegram_id == telegram_id)
            .with_for_update()
        )
        if user is None:
            raise LookupError("User not found")
        user.trial_disabled = disabled
        add_audit_log(
            self.session,
            action="admin_trial_disabled" if disabled else "admin_trial_enabled",
            entity_type="user",
            entity_id=user.id,
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            details={"target_telegram_id": telegram_id},
        )
        await self.session.flush()
        return user
