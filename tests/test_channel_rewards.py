import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatMemberStatus

from app.bot.handlers.channel_rewards import is_channel_member
from app.database.models import (
    ChannelReward,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    TrialActivation,
    User,
)
from app.services.channel_rewards import (
    CHANNEL_REWARD_DAYS,
    ChannelRewardService,
)
from app.services.subscriptions import ProvisioningResult, SubscriptionService


def make_user() -> User:
    return User(
        id=7,
        telegram_id=700,
        referral_code="channel-reward-user",
    )


@pytest.mark.asyncio
async def test_channel_reward_is_granted_once() -> None:
    user = make_user()
    subscription = Subscription(
        user_id=user.id,
        source_type=SubscriptionSource.paid,
        status=SubscriptionStatus.active,
        started_at=datetime(2026, 8, 1, tzinfo=UTC),
        expires_at=datetime(2026, 10, 1, tzinfo=UTC),
        device_limit=1,
    )
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[user, None, user, ChannelReward()])
    session.flush = AsyncMock()
    subscriptions = MagicMock()
    subscriptions.extend_with_bonus_days = AsyncMock(return_value=subscription)
    service = ChannelRewardService(session, subscriptions)

    first = await service.grant(user.telegram_id)
    second = await service.grant(user.telegram_id)

    assert first.awarded is True
    assert first.reward is not None
    assert first.reward.bonus_days == 7
    assert second.awarded is False
    assert second.reason == "already_claimed"
    subscriptions.extend_with_bonus_days.assert_awaited_once()
    assert subscriptions.extend_with_bonus_days.await_args.kwargs[
        "create_if_missing"
    ] is True


@pytest.mark.asyncio
async def test_channel_reward_creates_seven_day_subscription_when_missing() -> None:
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    user = make_user()
    session = MagicMock()
    session.scalar = AsyncMock(return_value=None)
    session.flush = AsyncMock()
    adapter = MagicMock()
    adapter.provision_bonus = AsyncMock(
        return_value=ProvisioningResult(status=SubscriptionStatus.active)
    )

    subscription = await SubscriptionService(
        session,
        adapter,
    ).extend_with_bonus_days(
        user,
        CHANNEL_REWARD_DAYS,
        redemption_id=uuid.uuid4(),
        create_if_missing=True,
        now=now,
    )

    assert subscription.source_type == SubscriptionSource.promo
    assert subscription.started_at == now
    assert subscription.expires_at == now + timedelta(days=7)
    assert subscription.status == SubscriptionStatus.active
    adapter.provision_bonus.assert_awaited_once()


@pytest.mark.asyncio
async def test_trial_keeps_channel_reward_days() -> None:
    now = datetime(2026, 9, 15, 12, tzinfo=UTC)
    user = make_user()
    subscription = Subscription(
        user_id=user.id,
        source_type=SubscriptionSource.promo,
        status=SubscriptionStatus.active,
        started_at=now,
        expires_at=now + timedelta(days=7),
        device_limit=1,
    )
    activation = TrialActivation(
        user_id=user.id,
        telegram_id=user.telegram_id,
        started_at=now,
        expires_at=now + timedelta(days=15),
    )
    session = MagicMock()
    session.scalar = AsyncMock(return_value=subscription)
    session.flush = AsyncMock()
    adapter = MagicMock()
    adapter.provision = AsyncMock(
        return_value=ProvisioningResult(status=SubscriptionStatus.active)
    )

    result = await SubscriptionService(session, adapter).register_trial(
        user,
        activation,
    )

    assert result.expires_at == now + timedelta(days=22)
    assert activation.expires_at == result.expires_at


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("status", "is_member", "expected"),
    [
        (ChatMemberStatus.MEMBER, None, True),
        (ChatMemberStatus.ADMINISTRATOR, None, True),
        (ChatMemberStatus.LEFT, None, False),
        (ChatMemberStatus.RESTRICTED, True, True),
        (ChatMemberStatus.RESTRICTED, False, False),
    ],
)
async def test_channel_membership_statuses(
    status: ChatMemberStatus,
    is_member: bool | None,
    expected: bool,
) -> None:
    bot = MagicMock()
    bot.get_chat_member = AsyncMock(
        return_value=SimpleNamespace(status=status, is_member=is_member)
    )

    assert await is_channel_member(bot, 700) is expected
    bot.get_chat_member.assert_awaited_once_with("@blazeVPNgroup", 700)
