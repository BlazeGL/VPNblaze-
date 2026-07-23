import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.database.models import (
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)
from app.integrations.remnawave.client import RemnawaveClient
from app.services.billing import BillingService

logger = logging.getLogger(__name__)


async def run_daily_billing(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    stop_event: asyncio.Event,
    remnawave_client: RemnawaveClient | None = None,
    interval_seconds: int = 3600,
) -> None:
    """Run frequently; transaction idempotency guarantees one charge per UTC day."""
    while not stop_event.is_set():
        try:
            await bill_active_subscriptions(
                session_factory=session_factory,
                bot=bot,
                remnawave_client=remnawave_client,
            )
        except Exception:
            logger.exception("Daily billing loop failed")
        try:
            await asyncio.wait_for(
                stop_event.wait(), timeout=interval_seconds
            )
        except TimeoutError:
            pass


async def bill_active_subscriptions(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    remnawave_client: RemnawaveClient | None = None,
) -> None:
    day = datetime.now(UTC).date()
    async with session_factory() as session:
        subscription_ids = list(
            await session.scalars(
                select(Subscription.id).where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.source_type != SubscriptionSource.trial,
                )
            )
        )

    for subscription_id in subscription_ids:
        user_telegram_id: int | None = None
        async with session_factory() as session, session.begin():
            result = await BillingService(
                session, remnawave_client
            ).charge_subscription(subscription_id, billing_date=day)
            if result.disabled:
                subscription = await session.get(
                    Subscription, subscription_id
                )
                user = (
                    await session.get(User, subscription.user_id)
                    if subscription is not None
                    else None
                )
                user_telegram_id = (
                    user.telegram_id if user is not None else None
                )
        if user_telegram_id is not None:
            try:
                await bot.send_message(
                    user_telegram_id,
                    "⚫ VPN отключён: на балансе недостаточно 5 ₽ для "
                    "суточного списания. Пополните баланс и активируйте VPN снова.",
                )
            except Exception:
                logger.exception(
                    "Could not notify user %s about billing suspension",
                    user_telegram_id,
                )
