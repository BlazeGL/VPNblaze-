import logging
from pathlib import Path

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.filters import CommandObject, CommandStart
from aiogram.types import CallbackQuery, FSInputFile, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.start import (
    BACK_TO_MAIN_CALLBACK,
    CHANNEL_CALLBACK,
    MAIN_MENU_CALLBACK,
    PRIVACY_POLICY_CALLBACK,
    REFUND_TERMS_CALLBACK,
    START_CONNECTION_CALLBACK,
    USER_AGREEMENT_CALLBACK,
    agreement_menu,
    build_connection_menu,
    build_main_menu,
    channel_menu,
    legal_page_menu,
)
from app.bot.keyboards.subscription import SUPPORT_URL
from app.bot.rendering import edit_text_or_caption
from app.bot.texts.start import (
    CHANNEL_TEXT,
    CONNECTION_MENU_TEXT,
    PRIVACY_POLICY_TEXT,
    REFUND_TERMS_TEXT,
    START_TEXT,
    USER_AGREEMENT_TEXT,
)
from app.core.config import Settings
from app.database.models import (
    Subscription,
    SubscriptionSource,
    TrialActivation,
)
from app.database.repositories import UserRepository
from app.services.referrals import ReferralService

logger = logging.getLogger(__name__)
router = Router(name=__name__)
START_BANNER_PATH = (
    Path(__file__).resolve().parents[2] / "assets" / "blazevpn-start-banner.png"
)


async def send_welcome(
    message: Message,
    user_agreement_url: str | None = None,
    support_url: str = SUPPORT_URL,
) -> None:
    if message.photo:
        await edit_text_or_caption(
            message,
            START_TEXT,
            build_main_menu(
                user_agreement_url,
                show_bonuses=True,
                support_url=support_url,
            ),
            parse_mode=ParseMode.HTML,
        )
        return
    await message.answer_photo(
        photo=FSInputFile(START_BANNER_PATH),
        caption=START_TEXT,
        reply_markup=build_main_menu(
            user_agreement_url,
            show_bonuses=True,
            support_url=support_url,
        ),
        parse_mode=ParseMode.HTML,
    )


@router.message(CommandStart())
async def handle_start(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    admin_ids: set[int],
    settings: Settings,
    command: CommandObject,
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    async with session_factory() as session, session.begin():
        repository = UserRepository(session)
        user, created = await repository.get_or_create(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
            is_admin=telegram_user.id in admin_ids,
        )
        user.is_admin = telegram_user.id in admin_ids
        referral = (
            await ReferralService(session).award_registration_bonus(
                user, command.args
            )
            if created
            else None
        )
    logger.info("Processed /start for Telegram user %s", telegram_user.id)
    if referral and referral.awarded and referral.referrer is not None:
        try:
            await message.bot.send_message(
                referral.referrer.telegram_id,
                "🎁 Вам начислен реферальный бонус 50 ₽.",
            )
        except Exception:
            logger.exception(
                "Could not notify referrer %s", referral.referrer.id
            )
    await send_welcome(
        message,
        settings.user_agreement_url,
        settings.support_url,
    )


@router.callback_query(F.data == START_CONNECTION_CALLBACK)
async def show_connection_menu(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(callback.from_user.id)
        subscription = (
            await session.scalar(
                select(Subscription).where(Subscription.user_id == user.id)
            )
            if user is not None
            else None
        )
        trial_activation = (
            await session.scalar(
                select(TrialActivation.id).where(
                    TrialActivation.telegram_id == user.telegram_id
                )
            )
            if user is not None
            else None
        )
    if user is None:
        await callback.answer("Сначала нажмите /start.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            CONNECTION_MENU_TEXT,
            build_connection_menu(
                trial_available=(
                    not user.trial_used
                    and not user.trial_disabled
                    and not user.is_blocked
                    and trial_activation is None
                    and (
                        subscription is None
                        or subscription.source_type != SubscriptionSource.paid
                    )
                ),
                has_subscription=subscription is not None,
            ),
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(
    F.data.in_({MAIN_MENU_CALLBACK, BACK_TO_MAIN_CALLBACK})
)
async def show_main_menu(callback: CallbackQuery, settings: Settings) -> None:
    await callback.answer()
    if callback.message:
        await send_welcome(
            callback.message,
            settings.user_agreement_url,
            settings.support_url,
        )


@router.callback_query(F.data == CHANNEL_CALLBACK)
async def show_channel(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            CHANNEL_TEXT,
            channel_menu(),
        )


@router.callback_query(F.data == USER_AGREEMENT_CALLBACK)
async def show_user_agreement(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            USER_AGREEMENT_TEXT,
            agreement_menu(),
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == PRIVACY_POLICY_CALLBACK)
async def show_privacy_policy(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            PRIVACY_POLICY_TEXT,
            legal_page_menu(),
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == REFUND_TERMS_CALLBACK)
async def show_refund_terms(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            REFUND_TERMS_TEXT,
            legal_page_menu(),
            parse_mode=ParseMode.HTML,
        )
