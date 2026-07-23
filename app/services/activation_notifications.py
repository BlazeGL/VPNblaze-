import logging
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.subscription import activation_keyboard
from app.bot.texts.subscription import activation_text
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import (
    ProvisioningStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.services.audit import add_audit_log

logger = logging.getLogger(__name__)

ActivationSender = Callable[
    [int, str, InlineKeyboardMarkup | None, ParseMode | None],
    Awaitable[None],
]


async def deliver_activation_notification(
    session: AsyncSession,
    *,
    subscription_id: uuid.UUID,
    cipher: SubscriptionUrlCipher | None,
    sender: ActivationSender,
) -> bool:
    """Send and persist one activation notification while holding a row lock."""
    subscription = await session.scalar(
        select(Subscription)
        .where(Subscription.id == subscription_id)
        .with_for_update()
    )
    if subscription is None:
        return False
    user = await session.get(User, subscription.user_id)
    if user is None:
        return False
    if (
        subscription.status != SubscriptionStatus.active
        or subscription.provisioning_status != ProvisioningStatus.active
    ):
        return False
    if subscription.activation_notified_at is not None:
        logger.debug(
            "activation already processed user_id=%s",
            user.id,
        )
        return False

    url = None
    if cipher is not None and subscription.subscription_url_encrypted:
        url = cipher.decrypt(subscription.subscription_url_encrypted)
    text = (
        activation_text(subscription, url)
        if url
        else "✅ VPN-доступ активирован. Персональная ссылка ещё готовится."
    )
    await sender(
        user.telegram_id,
        text,
        activation_keyboard() if url else None,
        ParseMode.HTML if url else None,
    )

    subscription.activation_notified_at = datetime.now(UTC)
    add_audit_log(
        session,
        action="activation_notification_sent",
        entity_type="subscription",
        entity_id=subscription.id,
        actor_user_id=user.id,
        actor_telegram_id=user.telegram_id,
    )
    logger.info("activation notification sent user_id=%s", user.id)
    await session.flush()
    return True


async def send_activation_notification(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    bot: Bot,
    subscription_id: uuid.UUID,
    cipher: SubscriptionUrlCipher | None,
) -> bool:
    async def sender(
        telegram_id: int,
        text: str,
        reply_markup: InlineKeyboardMarkup | None,
        parse_mode: ParseMode | None,
    ) -> None:
        await bot.send_message(
            telegram_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )

    async with session_factory() as session, session.begin():
        return await deliver_activation_notification(
            session,
            subscription_id=subscription_id,
            cipher=cipher,
            sender=sender,
        )
