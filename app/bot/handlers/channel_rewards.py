import logging
from dataclasses import dataclass

from aiogram import Bot, F, Router
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramAPIError
from aiogram.types import CallbackQuery
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.start import (
    CLAIM_CHANNEL_REWARD_CALLBACK,
    channel_menu,
)
from app.bot.rendering import edit_text_or_caption
from app.bot.texts.subscription import format_expiration
from app.core.crypto import SubscriptionUrlCipher
from app.integrations.remnawave.client import RemnawaveClient
from app.services.channel_rewards import (
    CHANNEL_REWARD_DAYS,
    ChannelRewardResult,
    ChannelRewardService,
)
from app.services.remnawave_factory import build_subscription_service

logger = logging.getLogger(__name__)
router = Router(name=__name__)
CHANNEL_CHAT_ID = "@blazeVPNgroup"


@dataclass(frozen=True, slots=True)
class ChannelClaimOutcome:
    reason: str
    reward_result: ChannelRewardResult | None = None


async def is_channel_member(bot: Bot, telegram_id: int) -> bool:
    member = await bot.get_chat_member(CHANNEL_CHAT_ID, telegram_id)
    if member.status in {
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
        ChatMemberStatus.MEMBER,
    }:
        return True
    return bool(
        member.status == ChatMemberStatus.RESTRICTED
        and getattr(member, "is_member", False)
    )


async def claim_channel_reward_for_user(
    *,
    bot: Bot,
    telegram_id: int,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None,
    subscription_cipher: SubscriptionUrlCipher | None,
    remnawave_internal_squad_uuid: str | None,
    remnawave_russia_squad_uuid: str | None,
    remnawave_template_user_uuid: str | None,
) -> ChannelClaimOutcome:
    try:
        subscribed = await is_channel_member(bot, telegram_id)
    except TelegramAPIError:
        logger.exception(
            "Could not check official channel membership for Telegram user %s",
            telegram_id,
        )
        return ChannelClaimOutcome("check_failed")
    if not subscribed:
        return ChannelClaimOutcome("not_subscribed")

    try:
        async with session_factory() as session, session.begin():
            subscription_service = build_subscription_service(
                session,
                remnawave_client,
                subscription_cipher,
                remnawave_internal_squad_uuid,
                remnawave_russia_squad_uuid,
                remnawave_template_user_uuid,
            )
            result = await ChannelRewardService(
                session,
                subscription_service,
            ).grant(telegram_id)
    except IntegrityError:
        return ChannelClaimOutcome("already_claimed")
    return ChannelClaimOutcome(result.reason, reward_result=result)


def channel_claim_text(outcome: ChannelClaimOutcome) -> str:
    result = outcome.reward_result
    if outcome.reason == "awarded" and result and result.subscription:
        return (
            f"✅ <b>Начислено +{CHANNEL_REWARD_DAYS} дней!</b>\n\n"
            "Спасибо за подписку на канал BlazeVPN.\n\n"
            "VPN-доступ действует до:\n"
            f"<b>{format_expiration(result.subscription.expires_at)}</b>"
        )
    messages = {
        "not_subscribed": (
            "Сначала подпишитесь на официальный канал BlazeVPN, затем "
            "вернитесь сюда и снова нажмите «Получить +7 дней»."
        ),
        "check_failed": (
            "Не удалось проверить подписку на канал. Попробуйте ещё раз позже."
        ),
        "already_claimed": "✅ Вы уже получили 7 бонусных дней за подписку.",
        "blocked": "Бонус недоступен для заблокированной учётной записи.",
        "user_not_found": "Сначала нажмите /start.",
    }
    return messages.get(outcome.reason, "Не удалось начислить бонус. Попробуйте позже.")


@router.callback_query(F.data == CLAIM_CHANNEL_REWARD_CALLBACK)
async def claim_channel_reward(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
    remnawave_russia_squad_uuid: str | None = None,
    remnawave_template_user_uuid: str | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Откройте бота в личных сообщениях.", show_alert=True
        )
        return
    await callback.answer("Проверяем подписку…")
    outcome = await claim_channel_reward_for_user(
        bot=callback.bot,
        telegram_id=callback.from_user.id,
        session_factory=session_factory,
        remnawave_client=remnawave_client,
        subscription_cipher=subscription_cipher,
        remnawave_internal_squad_uuid=remnawave_internal_squad_uuid,
        remnawave_russia_squad_uuid=remnawave_russia_squad_uuid,
        remnawave_template_user_uuid=remnawave_template_user_uuid,
    )
    await edit_text_or_caption(
        callback.message,
        channel_claim_text(outcome),
        channel_menu(),
        parse_mode=ParseMode.HTML,
    )
