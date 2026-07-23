import asyncio
import logging
from datetime import UTC, datetime

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.subscription import activation_keyboard
from app.bot.texts.subscription import activation_text
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    ProvisioningStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.integrations.onlipay.client import OnliPayClient
from app.integrations.remnawave.client import RemnawaveClient
from app.services.audit import add_audit_log
from app.services.payments import PaymentService, PaymentValidationError
from app.services.remnawave import RemnawaveProvisioningService
from app.services.remnawave_factory import build_subscription_service

logger = logging.getLogger(__name__)


async def retry_subscription_activations(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    admin_ids: set[int],
    stop_event: asyncio.Event,
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    internal_squad_uuid: str | None = None,
    interval_seconds: int = 60,
) -> None:
    while not stop_event.is_set():
        try:
            await _retry_paid_orders(
                session_factory,
                bot,
                admin_ids,
                remnawave_client,
                subscription_cipher,
                internal_squad_uuid,
            )
            if remnawave_client and subscription_cipher:
                await _retry_subscriptions(
                    session_factory,
                    bot,
                    admin_ids,
                    remnawave_client,
                    subscription_cipher,
                    internal_squad_uuid,
                )
                await _disable_expired(session_factory, remnawave_client)
        except Exception:
            logger.exception("Subscription activation retry loop failed")
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            pass


async def _retry_paid_orders(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    admin_ids: set[int],
    client: RemnawaveClient | None,
    cipher: SubscriptionUrlCipher | None,
    squad: str | None,
) -> None:
    async with session_factory() as session:
        ids = list(
            await session.scalars(
                select(Payment.provider_payment_id)
                .join(Order, Order.id == Payment.order_id)
                .where(
                    Payment.status == PaymentStatus.paid,
                    Payment.processed_at.is_(None),
                    Order.status == OrderStatus.processing,
                )
                .limit(20)
            )
        )
    for provider_id in ids:
        try:
            async with session_factory() as session, session.begin():
                payment = await session.scalar(
                    select(Payment).where(Payment.provider_payment_id == provider_id)
                )
                if not payment:
                    continue
                order = await session.get(Order, payment.order_id)
                if not order:
                    continue
                result = await PaymentService(
                    session,
                    OnliPayClient(),
                    subscription_service=build_subscription_service(
                        session, client, cipher, squad
                    ),
                ).process_confirmed_payment(
                    provider_payment_id=provider_id,
                    reported_order_id=str(order.id),
                    amount=payment.amount,
                    currency=payment.currency,
                    sanitized_payload=payment.provider_payload_sanitized,
                    webhook_received=False,
                )
                user = await session.get(User, order.user_id)
                if (
                    result.completed
                    and result.subscription
                    and result.subscription.subscription_url_encrypted
                    and user
                ):
                    add_audit_log(
                        session,
                        action="subscription_url_sent",
                        entity_type="subscription",
                        entity_id=result.subscription.id,
                        actor_user_id=user.id,
                        actor_telegram_id=user.telegram_id,
                    )
            if result.completed and user:
                url = None
                if (
                    cipher
                    and result.subscription
                    and result.subscription.subscription_url_encrypted
                ):
                    url = cipher.decrypt(result.subscription.subscription_url_encrypted)
                await bot.send_message(
                    user.telegram_id,
                    (
                        activation_text(result.subscription, url)
                        if url and result.subscription
                        else "✅ Активация завершена. Ссылка ещё готовится."
                    ),
                    reply_markup=activation_keyboard() if url else None,
                    parse_mode=ParseMode.HTML if url else None,
                )
            elif not result.completed:
                for admin_id in admin_ids:
                    await bot.send_message(
                        admin_id, f"⚠️ Ошибка активации заказа {str(order.id)[:8]}."
                    )
        except PaymentValidationError as exc:
            logger.warning("Paid order retry rejected: %s", exc.reason)


async def _retry_subscriptions(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    admin_ids: set[int],
    client: RemnawaveClient,
    cipher: SubscriptionUrlCipher,
    squad: str | None,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        ids = list(
            await session.scalars(
                select(Subscription.id)
                .where(
                    Subscription.status.in_(
                        [
                            SubscriptionStatus.pending,
                            SubscriptionStatus.activation_failed,
                        ]
                    ),
                    Subscription.activation_attempts < 5,
                    (
                        Subscription.next_retry_at.is_(None)
                        | (Subscription.next_retry_at <= now)
                    ),
                )
                .limit(20)
            )
        )
    for item_id in ids:
        async with session_factory() as session, session.begin():
            item = await session.scalar(
                select(Subscription)
                .where(Subscription.id == item_id)
                .with_for_update(skip_locked=True)
            )
            if not item:
                continue
            user = await session.get(User, item.user_id)
            if not user:
                continue
            result = await RemnawaveProvisioningService(
                session, client, cipher, squad
            ).provision(item, user, source=item.source_type, order_id=item.order_id)
            attempts = item.activation_attempts
            encrypted_url = item.subscription_url_encrypted
            if result.status == SubscriptionStatus.active and encrypted_url:
                add_audit_log(
                    session,
                    action="subscription_url_sent",
                    entity_type="subscription",
                    entity_id=item.id,
                    actor_user_id=user.id,
                    actor_telegram_id=user.telegram_id,
                )
        if result.status == SubscriptionStatus.active:
            url = cipher.decrypt(encrypted_url) if encrypted_url else None
            await bot.send_message(
                user.telegram_id,
                (
                    activation_text(item, url)
                    if url
                    else "✅ VPN-доступ активирован. Ссылка ещё готовится."
                ),
                reply_markup=activation_keyboard() if url else None,
                parse_mode=ParseMode.HTML if url else None,
            )
        elif attempts >= 5:
            for admin_id in admin_ids:
                await bot.send_message(
                    admin_id, f"⚠️ Исчерпаны попытки активации {str(item_id)[:8]}."
                )


async def _disable_expired(
    session_factory: async_sessionmaker[AsyncSession], client: RemnawaveClient
) -> None:
    async with session_factory() as session:
        ids = list(
            await session.scalars(
                select(Subscription.id)
                .where(
                    Subscription.expires_at <= datetime.now(UTC),
                    Subscription.status != SubscriptionStatus.expired,
                    Subscription.remnawave_user_uuid.is_not(None),
                )
                .limit(50)
            )
        )
    for item_id in ids:
        async with session_factory() as session, session.begin():
            item = await session.scalar(
                select(Subscription)
                .where(Subscription.id == item_id)
                .with_for_update(skip_locked=True)
            )
            if item and item.remnawave_user_uuid:
                await client.disable_user(item.remnawave_user_uuid)
                item.status = SubscriptionStatus.expired
                item.provisioning_status = ProvisioningStatus.disabled
                item.remnawave_status = "DISABLED"
                add_audit_log(
                    session,
                    action="remnawave_user_disabled",
                    entity_type="subscription",
                    entity_id=item.id,
                    actor_user_id=item.user_id,
                )
