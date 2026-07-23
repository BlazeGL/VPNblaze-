import asyncio
import logging
from datetime import UTC, datetime, timedelta

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.tariffs import money
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    ProvisioningStatus,
    Subscription,
    SubscriptionStatus,
    Tariff,
    User,
)
from app.integrations.onlipay.client import OnliPayClient
from app.integrations.remnawave.client import RemnawaveClient
from app.services.activation_notifications import send_activation_notification
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
            await _send_pending_activation_notifications(
                session_factory,
                bot,
                subscription_cipher,
            )
            await _disable_expired(session_factory, remnawave_client)
            await _send_expiry_notifications(session_factory, bot)
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
            subscription_id = None
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
                if result.completed and result.subscription and user:
                    subscription_id = result.subscription.id
            if subscription_id is not None:
                await send_activation_notification(
                    session_factory,
                    bot=bot,
                    subscription_id=subscription_id,
                    cipher=cipher,
                )
            elif not result.completed:
                for admin_id in admin_ids:
                    await bot.send_message(
                        admin_id, f"⚠️ Ошибка активации заказа {str(order.id)[:8]}."
                    )
        except PaymentValidationError as exc:
            logger.warning("Paid order retry rejected: %s", exc.reason)
        except Exception:
            logger.exception(
                "Could not send activation notification for payment %s",
                provider_id,
            )


async def _send_expiry_notifications(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
) -> None:
    now = datetime.now(UTC)
    async with session_factory() as session:
        renewal = (
            await session.execute(
                select(Tariff.price, Tariff.currency, Tariff.duration_days)
                .where(Tariff.is_active.is_(True))
                .order_by(Tariff.sort_order, Tariff.id)
                .limit(1)
            )
        ).first()
        ids = list(
            await session.scalars(
                select(Subscription.id)
                .where(
                    Subscription.status.in_(
                        [
                            SubscriptionStatus.active,
                            SubscriptionStatus.expired,
                        ]
                    ),
                    Subscription.expires_at <= now + timedelta(days=3),
                )
                .limit(100)
            )
        )
    renewal_offer = (
        (
            "\n\nСтоимость продления: "
            f"<b>{money(renewal.price, renewal.currency)} "
            f"за {renewal.duration_days} дней</b>"
        )
        if renewal is not None
        else ""
    )
    for item_id in ids:
        async with session_factory() as session, session.begin():
            item = await session.scalar(
                select(Subscription)
                .where(Subscription.id == item_id)
                .with_for_update(skip_locked=True)
            )
            if item is None:
                continue
            user = await session.get(User, item.user_id)
            if user is None:
                continue
            current = datetime.now(UTC)
            if item.expires_at <= current:
                field = "expired_notice_at"
                text = (
                    "⚫ <b>Подписка закончилась</b>\n\n"
                    "VPN-доступ отключён после окончания оплаченного срока."
                    f"{renewal_offer}"
                )
            elif item.expires_at <= current + timedelta(days=1):
                field = "expiry_notice_1d_at"
                text = (
                    "⚠️ <b>Подписка закончится через 1 день</b>\n\n"
                    "Продлите BlazeVPN заранее, чтобы подключение не "
                    f"прерывалось.{renewal_offer}"
                )
            else:
                field = "expiry_notice_3d_at"
                text = (
                    "⚠️ <b>Подписка закончится через 3 дня</b>\n\n"
                    "Продлите BlazeVPN заранее, чтобы подключение не "
                    f"прерывалось.{renewal_offer}"
                )
            if getattr(item, field) is not None:
                continue
            await bot.send_message(
                user.telegram_id,
                text,
                parse_mode=ParseMode.HTML,
            )
            setattr(item, field, current)


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
        activated = False
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
            if result.status == SubscriptionStatus.active:
                activated = True
                if item.order_id is not None:
                    order = await session.get(Order, item.order_id)
                    if order is not None and order.status == OrderStatus.processing:
                        order.status = OrderStatus.completed
                        order.completed_at = datetime.now(UTC)
                        order.failure_reason = None
        if activated:
            try:
                await send_activation_notification(
                    session_factory,
                    bot=bot,
                    subscription_id=item_id,
                    cipher=cipher,
                )
            except Exception:
                logger.exception(
                    "Could not send activation notification for subscription %s",
                    item_id,
                )
        elif attempts >= 5:
            for admin_id in admin_ids:
                await bot.send_message(
                    admin_id, f"⚠️ Исчерпаны попытки активации {str(item_id)[:8]}."
                )


async def _send_pending_activation_notifications(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    cipher: SubscriptionUrlCipher | None,
) -> None:
    async with session_factory() as session:
        ids = list(
            await session.scalars(
                select(Subscription.id)
                .where(
                    Subscription.status == SubscriptionStatus.active,
                    Subscription.provisioning_status == ProvisioningStatus.active,
                    Subscription.activation_notified_at.is_(None),
                )
                .limit(100)
            )
        )
    for item_id in ids:
        try:
            await send_activation_notification(
                session_factory,
                bot=bot,
                subscription_id=item_id,
                cipher=cipher,
            )
        except Exception:
            logger.exception(
                "Activation notification delivery failed for subscription %s; "
                "it remains retryable",
                item_id,
            )


async def _disable_expired(
    session_factory: async_sessionmaker[AsyncSession],
    client: RemnawaveClient | None,
) -> None:
    async with session_factory() as session:
        ids = list(
            await session.scalars(
                select(Subscription.id)
                .where(
                    Subscription.expires_at <= datetime.now(UTC),
                    (
                        Subscription.status != SubscriptionStatus.expired
                    )
                    | (
                        Subscription.remnawave_user_uuid.is_not(None)
                        & (
                            Subscription.remnawave_status.is_(None)
                            | (Subscription.remnawave_status != "DISABLED")
                        )
                    ),
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
            if item:
                was_expired = item.status == SubscriptionStatus.expired
                remote_disabled = False
                if item.remnawave_user_uuid and client:
                    await client.disable_user(item.remnawave_user_uuid)
                    item.remnawave_status = "DISABLED"
                    remote_disabled = True
                item.status = SubscriptionStatus.expired
                item.provisioning_status = ProvisioningStatus.disabled
                if remote_disabled or not was_expired:
                    add_audit_log(
                        session,
                        action=(
                            "remnawave_user_disabled"
                            if remote_disabled
                            else "subscription_expired"
                        ),
                        entity_type="subscription",
                        entity_id=item.id,
                        actor_user_id=item.user_id,
                    )
