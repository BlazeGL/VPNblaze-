import logging

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.subscription import (
    activation_keyboard,
    back_keyboard,
    devices_keyboard,
    platform_keyboard,
    subscription_menu,
)
from app.bot.rendering import edit_text_or_caption
from app.bot.texts.account import get_account_state
from app.bot.texts.subscription import (
    DEVICES_TEXT,
    INSTRUCTION_TEXT,
    PLATFORM_TEXTS,
    activation_text,
    subscription_link_text,
)
from app.core.config import Settings
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import Subscription, User
from app.services.audit import add_audit_log

logger = logging.getLogger(__name__)
router = Router(name=__name__)

PLATFORM_CALLBACKS = {
    "app_android": ("android", "key"),
    "app_ios": ("ios", "key"),
    "app_windows": ("windows", "key"),
    "app_linux": ("linux", "key"),
    "app_android_main": ("android", "main"),
    "app_ios_main": ("ios", "main"),
    "app_windows_main": ("windows", "main"),
    "app_linux_main": ("linux", "main"),
    "app_android_subscription": ("android", "subscription"),
    "app_ios_subscription": ("ios", "subscription"),
    "app_windows_subscription": ("windows", "subscription"),
    "app_linux_subscription": ("linux", "subscription"),
}


async def edit_or_send(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
) -> None:
    await edit_text_or_caption(
        message,
        text,
        reply_markup,
        parse_mode=ParseMode.HTML,
    )


async def _owned_subscription(
    session: AsyncSession, telegram_id: int
) -> tuple[User | None, Subscription | None]:
    user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
    if user is None:
        return None, None
    subscription = await session.scalar(
        select(Subscription).where(Subscription.user_id == user.id)
    )
    return user, subscription


async def render_key(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    subscription_cipher: SubscriptionUrlCipher | None,
) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Для безопасности откройте бота в личных сообщениях.", show_alert=True
        )
        return
    async with session_factory() as session, session.begin():
        user, subscription = await _owned_subscription(session, callback.from_user.id)
        encrypted = subscription.subscription_url_encrypted if subscription else None
        state = get_account_state(subscription) if subscription is not None else "none"
        if (
            user
            and subscription
            and state in {"active", "trial"}
            and encrypted
            and subscription_cipher
        ):
            add_audit_log(
                session,
                action="subscription_url_sent",
                entity_type="subscription",
                entity_id=subscription.id,
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_id,
            )
    if subscription is None:
        trial_available = bool(
            user
            and not user.trial_used
            and not user.trial_disabled
            and not user.is_blocked
        )
        await callback.answer()
        await edit_or_send(
            callback.message,
            "🔑 <b>VPN-ключ пока не создан</b>\n\n"
            "Сначала активируйте бесплатный период или приобретите подписку.",
            subscription_menu(
                state="none",
                trial_available=trial_available,
                has_key=False,
            ),
        )
        return
    if state == "expired":
        await callback.answer()
        await edit_or_send(
            callback.message,
            "🔴 <b>Срок подписки закончился</b>\n\n"
            "После продления ваш прежний ключ снова заработает.",
            subscription_menu(state="expired", has_key=encrypted is not None),
        )
        return
    if state == "disabled":
        await callback.answer()
        await edit_or_send(
            callback.message,
            "⚫ <b>VPN-доступ отключён</b>\n\n"
            "Пополните баланс и активируйте подписку снова.",
            subscription_menu(state="disabled", has_key=encrypted is not None),
        )
        return
    if state not in {"active", "trial"} or encrypted is None:
        await callback.answer()
        await edit_or_send(
            callback.message,
            "⏳ <b>Ваш VPN-доступ готовится</b>\n\n"
            "Обычно это занимает меньше минуты. Попробуйте снова немного позже.",
            subscription_menu(state="pending", has_key=False),
        )
        return
    if subscription_cipher is None:
        await callback.answer("Ссылка временно недоступна.", show_alert=True)
        return
    try:
        url = subscription_cipher.decrypt(encrypted)
    except (TypeError, ValueError):
        logger.exception(
            "Could not decrypt subscription URL for Telegram user %s",
            callback.from_user.id,
        )
        await callback.answer("Ссылка временно недоступна.", show_alert=True)
        return
    await callback.answer()
    await edit_or_send(
        callback.message,
        activation_text(subscription, url),
        activation_keyboard(),
    )


@router.callback_query(F.data.in_({"key_refresh", "back_to_key"}))
async def show_key(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    await render_key(callback, session_factory, subscription_cipher)


@router.callback_query(F.data.in_({"apps", "apps_from_main", "apps_from_subscription"}))
async def show_devices(callback: CallbackQuery) -> None:
    source = {
        "apps": "key",
        "apps_from_main": "main",
        "apps_from_subscription": "subscription",
    }.get(callback.data or "")
    if source is None:
        await callback.answer("Неизвестный источник навигации.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await edit_or_send(
            callback.message, DEVICES_TEXT, devices_keyboard(source=source)
        )


@router.callback_query(
    F.data.in_(
        {
            "back_to_devices",
            "back_to_devices_main",
            "back_to_devices_subscription",
        }
    )
)
async def back_to_devices(callback: CallbackQuery) -> None:
    source = {
        "back_to_devices": "key",
        "back_to_devices_main": "main",
        "back_to_devices_subscription": "subscription",
    }.get(callback.data or "")
    if source is None:
        await callback.answer("Неизвестный источник навигации.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await edit_or_send(
            callback.message, DEVICES_TEXT, devices_keyboard(source=source)
        )


@router.callback_query(F.data.in_(set(PLATFORM_CALLBACKS)))
async def show_platform(callback: CallbackQuery, settings: Settings) -> None:
    selection = PLATFORM_CALLBACKS.get(callback.data or "")
    if selection is None:
        await callback.answer("Неизвестная платформа.", show_alert=True)
        return
    platform, source = selection
    urls = {
        "android": settings.android_app_url,
        "ios": settings.ios_app_url,
        "windows": settings.windows_app_url,
        "linux": settings.linux_app_url,
    }
    await callback.answer()
    if callback.message:
        await edit_or_send(
            callback.message,
            PLATFORM_TEXTS[platform],
            platform_keyboard(
                platform,
                urls[platform],
                back_callback=(
                    "back_to_devices"
                    if source == "key"
                    else f"back_to_devices_{source}"
                ),
            ),
        )


@router.callback_query(F.data == "subscription_link")
async def show_short_link(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Для безопасности откройте бота в личных сообщениях.", show_alert=True
        )
        return
    async with session_factory() as session, session.begin():
        user, subscription = await _owned_subscription(session, callback.from_user.id)
        encrypted = subscription.subscription_url_encrypted if subscription else None
        if user and subscription and encrypted and subscription_cipher:
            add_audit_log(
                session,
                action="subscription_url_sent",
                entity_type="subscription",
                entity_id=subscription.id,
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_id,
            )
    if encrypted is None or subscription_cipher is None:
        await callback.answer(
            "Ссылка ещё готовится. Попробуйте позже.", show_alert=True
        )
        return
    try:
        url = subscription_cipher.decrypt(encrypted)
    except (TypeError, ValueError):
        logger.exception("Could not decrypt a subscription URL")
        await callback.answer("Ссылка временно недоступна.", show_alert=True)
        return
    await callback.answer()
    await callback.message.answer(
        subscription_link_text(url), parse_mode=ParseMode.HTML
    )


@router.callback_query(F.data == "key_instruction")
async def show_instruction(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await edit_or_send(
            callback.message, INSTRUCTION_TEXT, back_keyboard("back_to_key")
        )
