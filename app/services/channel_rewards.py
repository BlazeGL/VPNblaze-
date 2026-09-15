from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import ChannelReward, Subscription, User
from app.services.audit import add_audit_log
from app.services.subscriptions import SubscriptionService

CHANNEL_REWARD_CAMPAIGN = "official_channel_2026_09"
CHANNEL_REWARD_DAYS = 7


@dataclass(frozen=True, slots=True)
class ChannelRewardResult:
    awarded: bool
    reason: str
    reward: ChannelReward | None = None
    subscription: Subscription | None = None


class ChannelRewardService:
    def __init__(
        self,
        session: AsyncSession,
        subscription_service: SubscriptionService,
    ) -> None:
        self.session = session
        self.subscription_service = subscription_service

    async def grant(
        self,
        telegram_id: int,
        *,
        now: datetime | None = None,
    ) -> ChannelRewardResult:
        moment = now or datetime.now(UTC)
        user = await self.session.scalar(
            select(User).where(User.telegram_id == telegram_id).with_for_update()
        )
        if user is None:
            return ChannelRewardResult(False, "user_not_found")
        if user.is_blocked:
            return ChannelRewardResult(False, "blocked")

        existing = await self.session.scalar(
            select(ChannelReward).where(
                ChannelReward.user_id == user.id,
                ChannelReward.campaign == CHANNEL_REWARD_CAMPAIGN,
            )
        )
        if existing is not None:
            return ChannelRewardResult(False, "already_claimed", reward=existing)

        reward = ChannelReward(
            user_id=user.id,
            campaign=CHANNEL_REWARD_CAMPAIGN,
            bonus_days=CHANNEL_REWARD_DAYS,
        )
        self.session.add(reward)
        await self.session.flush()
        subscription = await self.subscription_service.extend_with_bonus_days(
            user,
            CHANNEL_REWARD_DAYS,
            redemption_id=reward.id,
            create_if_missing=True,
            now=moment,
        )
        add_audit_log(
            self.session,
            action="channel_reward_granted",
            entity_type="channel_reward",
            entity_id=reward.id,
            actor_user_id=user.id,
            actor_telegram_id=user.telegram_id,
            details={
                "campaign": CHANNEL_REWARD_CAMPAIGN,
                "bonus_days": CHANNEL_REWARD_DAYS,
                "expires_at": subscription.expires_at.isoformat(),
            },
        )
        await self.session.flush()
        return ChannelRewardResult(
            True,
            "awarded",
            reward=reward,
            subscription=subscription,
        )
