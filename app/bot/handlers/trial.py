from datetime import UTC, datetime
from math import ceil

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.types import CallbackQuery
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.subscription import subscription_menu
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import Subscription, Tariff, User
from app.integrations.remnawave.client import RemnawaveClient
from app.services.audit import add_audit_log
from app.services.remnawave_factory import build_subscription_service
from app.services.remnawave_sync import RemnawaveSyncService
from app.services.trials import TrialService

router = Router(name=__name__)


def format_utc(value: object) -> str:
    return value.astimezone(UTC).strftime("%d.%m.%Y %H:%M")  # type: ignore[union-attr]


@router.callback_query(F.data == "activate_trial")
async def activate_trial(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
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
            )
            result = await TrialService(session, service).activate(
                callback.from_user.id
            )
            if (
                result.subscription is not None
                and result.subscription.subscription_url_encrypted
            ):
                add_audit_log(
                    session,
                    action="subscription_url_sent",
                    entity_type="subscription",
                    entity_id=result.subscription.id,
                    actor_telegram_id=callback.from_user.id,
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
        if result.subscription.subscription_url_encrypted and subscription_cipher:
            url = subscription_cipher.decrypt(
                result.subscription.subscription_url_encrypted
            )
            await callback.message.answer(
                "🎁 Пробный период активирован\n\n"
                f"Доступ действует до: {format_utc(result.activation.expires_at)}\n\n"
                f"Ваша индивидуальная ссылка:\n\n{url}"
            )
        else:
            await callback.message.answer(
                "🎁 Пробный период зарегистрирован\n\n"
                f"Доступ действует до: {format_utc(result.activation.expires_at)}\n\n"
                "Доступ готовится. Повторно активировать trial не нужно."
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
        "paid_subscription_exists": "У вас уже есть действующая платная подписка.",
        "user_not_found": "Сначала нажмите /start.",
    }
    await callback.message.answer(
        messages.get(result.reason, "Пробный период недоступен.")
    )


@router.callback_query(F.data.in_({"my_subscription", "subscription_refresh"}))
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
                pass
        tariff = (
            await session.get(Tariff, subscription.tariff_id)
            if subscription and subscription.tariff_id
            else None
        )
    await callback.answer()
    if callback.message is None:
        return
    if subscription is None:
        await callback.message.answer("У вас пока нет зарегистрированной подписки.")
        return
    seconds = (subscription.expires_at - datetime.now(UTC)).total_seconds()
    days_left = max(0, ceil(seconds / 86400))
    traffic = (
        "Безлимит"
        if subscription.is_unlimited_traffic
        else f"{subscription.traffic_limit_gb or 0} ГБ"
    )
    used = (
        f"{subscription.used_traffic_bytes / 1024**3:.2f} ГБ"
        if subscription.used_traffic_bytes is not None
        else "нет данных"
    )
    await callback.message.answer(
        "Ваша подписка\n\n"
        f"Статус: {subscription.status.value}\n"
        f"Источник: {subscription.source_type.value}\n"
        f"Начало: {format_utc(subscription.started_at)} UTC\n"
        f"Окончание: {format_utc(subscription.expires_at)} UTC\n"
        f"Осталось дней: {days_left}\n"
        f"Тариф: {tariff.name if tariff else '—'}\n"
        f"Лимит трафика: {traffic}\n"
        f"Использовано: {used}\n"
        f"Лимит устройств: {subscription.device_limit}\n"
        f"Синхронизация Remnawave: {subscription.provisioning_status.value}",
        reply_markup=subscription_menu(),
    )


@router.callback_query(F.data == "subscription_link")
async def get_subscription_link(
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
        user = await session.scalar(
            select(User).where(User.telegram_id == callback.from_user.id)
        )
        subscription = (
            await session.scalar(
                select(Subscription).where(Subscription.user_id == user.id)
            )
            if user
            else None
        )
        if user and subscription and subscription.subscription_url_encrypted:
            add_audit_log(
                session,
                action="subscription_url_sent",
                entity_type="subscription",
                entity_id=subscription.id,
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_id,
            )
    await callback.answer()
    if not subscription or not subscription.subscription_url_encrypted:
        await callback.message.answer("Ссылка ещё готовится. Попробуйте позже.")
        return
    if subscription_cipher is None:
        await callback.message.answer("Ссылка временно недоступна.")
        return
    url = subscription_cipher.decrypt(subscription.subscription_url_encrypted)
    await callback.message.answer(f"Ваша индивидуальная ссылка:\n\n{url}")
