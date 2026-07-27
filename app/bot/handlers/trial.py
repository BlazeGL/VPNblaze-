import logging
from datetime import UTC

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.start import (
    ACTIVATE_TRIAL_CALLBACK,
    MAIN_MENU_CALLBACK,
    MY_SUBSCRIPTION_CALLBACK,
    build_connection_menu,
)
from app.bot.keyboards.subscription import activation_keyboard, subscription_menu
from app.bot.rendering import edit_text_or_caption
from app.bot.texts.account import account_text, empty_account_text
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import Subscription, SubscriptionStatus, Tariff, User
from app.integrations.remnawave.client import RemnawaveClient
from app.services.activation_notifications import deliver_activation_notification
from app.services.remnawave_factory import build_subscription_service
from app.services.remnawave_sync import RemnawaveSyncService
from app.services.trials import TrialService

router = Router(name=__name__)
logger = logging.getLogger(__name__)


def format_utc(value: object) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M")  # type: ignore[union-attr]


@router.callback_query(F.data == ACTIVATE_TRIAL_CALLBACK)
async def activate_trial(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
    remnawave_russia_squad_uuid: str | None = None,
    remnawave_template_user_uuid: str | None = None,
    admin_ids: set[int] | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Для безопасности откройте бота в личных сообщениях.", show_alert=True
        )
        return
    try:
        async with session_factory() as session, session.begin():
            service = build_subscription_service(
                session,
                remnawave_client,
                subscription_cipher,
                remnawave_internal_squad_uuid,
                remnawave_russia_squad_uuid,
                remnawave_template_user_uuid,
            )
            result = await TrialService(session, service).activate(
                callback.from_user.id
            )
    except IntegrityError:
        await callback.answer(
            "Вы уже использовали бесплатный пробный период.", show_alert=True
        )
        return
    await callback.answer()
    if callback.message is None:
        return
    if result.activated and result.activation is not None and result.subscription:
        if result.subscription.status == SubscriptionStatus.active:

            async def sender(
                _telegram_id: int,
                text: str,
                reply_markup: InlineKeyboardMarkup | None,
                parse_mode: ParseMode | None,
            ) -> None:
                await edit_text_or_caption(
                    callback.message,
                    text,
                    reply_markup or activation_keyboard(),
                    parse_mode=parse_mode,
                )

            async with session_factory() as session, session.begin():
                await deliver_activation_notification(
                    session,
                    subscription_id=result.subscription.id,
                    cipher=subscription_cipher,
                    sender=sender,
                )
        else:
            await edit_text_or_caption(
                callback.message,
                "🎁 Пробный период зарегистрирован\n\n"
                f"Доступ действует до: {format_utc(result.activation.expires_at)}\n\n"
                "Доступ готовится. Повторно активировать trial не нужно.",
                activation_keyboard(),
            )
            technical_reason = (
                result.subscription.last_activation_error
                or "техническая причина не указана"
            )[:500]
            for admin_id in admin_ids or set():
                await callback.bot.send_message(
                    admin_id,
                    f"⚠️ Trial пользователя {callback.from_user.id} ожидает "
                    f"Remnawave. Причина: {technical_reason}",
                )
        return
    messages = {
        "already_used": "Вы уже использовали бесплатный пробный период.",
        "disabled": "Пробный период недоступен для вашей учётной записи.",
        "blocked": "Пробный период недоступен для заблокированной учётной записи.",
        "paid_subscription_exists": (
            "Вы уже приобретали подписку; пробный период доступен только до "
            "первой покупки."
        ),
        "user_not_found": "Сначала нажмите /start.",
    }
    await edit_text_or_caption(
        callback.message,
        messages.get(result.reason, "Пробный период недоступен."),
        build_connection_menu(trial_available=False, has_subscription=False),
    )


@router.callback_query(
    F.data.in_(
        {
            MY_SUBSCRIPTION_CALLBACK,
            "my_subscription_from_key",
            "subscription_refresh",
            "back_to_subscription",
        }
    )
)
async def show_subscription(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Для безопасности откройте бота в личных сообщениях.", show_alert=True
        )
        return
    sync_unavailable = False
    async with session_factory() as session, session.begin():
        user = await session.scalar(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        subscription = (
            await session.scalar(
                select(Subscription).where(Subscription.user_id == user.id)
            )
            if user is not None
            else None
        )
        if (
            callback.data == "subscription_refresh"
            and user
            and subscription
            and remnawave_client
            and subscription_cipher
        ):
            try:
                await RemnawaveSyncService(
                    session, remnawave_client, subscription_cipher
                ).sync_one(subscription, user)
            except Exception:
                sync_unavailable = True
                logger.exception(
                    "Could not refresh subscription for Telegram user %s",
                    callback.from_user.id,
                )
        tariff = (
            await session.get(Tariff, subscription.tariff_id)
            if subscription and subscription.tariff_id
            else None
        )
    await callback.answer()
    if callback.message is None:
        return
    if subscription is None:
        await edit_text_or_caption(
            callback.message,
            empty_account_text(user),
            subscription_menu(
                state="none",
                trial_available=bool(
                    user
                    and not user.trial_used
                    and not user.trial_disabled
                    and not user.is_blocked
                ),
                has_key=False,
                back_callback=MAIN_MENU_CALLBACK,
            ),
            parse_mode=ParseMode.HTML,
        )
        return
    text, state = account_text(
        subscription,
        tariff.name if tariff else None,
        tariff_price=tariff.price if tariff else None,
        tariff_currency=tariff.currency if tariff else "RUB",
        user=user,
        sync_unavailable=(
            sync_unavailable or bool(subscription.remnawave_sync_error)
        ),
    )
    await edit_text_or_caption(
        callback.message,
        text,
        subscription_menu(
            state=state,
            has_key=subscription.subscription_url_encrypted is not None,
            back_callback=(
                "back_to_key"
                if callback.data == "my_subscription_from_key"
                else MAIN_MENU_CALLBACK
            )
        ),
        parse_mode=ParseMode.HTML,
    )
