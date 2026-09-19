import asyncio
import logging
from datetime import UTC, datetime, timedelta
from enum import StrEnum
from zoneinfo import ZoneInfo

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramForbiddenError
from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.start import (
    HELP_CENTER_CALLBACK,
    MY_SUBSCRIPTION_CALLBACK,
    START_CONNECTION_CALLBACK,
    TARIFFS_CALLBACK,
)
from app.database.models import (
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.remnawave.exceptions import RemnawaveError
from app.services.audit import add_audit_log

logger = logging.getLogger(__name__)

MOSCOW = ZoneInfo("Europe/Moscow")
DELIVERY_START_HOUR = 11
DELIVERY_END_HOUR = 20


class TrialReminderKind(StrEnum):
    connection = "connection"
    ending = "ending"
    expired = "expired"


TRIAL_REMINDER_TEXTS = {
    TrialReminderKind.connection: (
        "Получилось подключиться? 🚀\n\n"
        "Если что-то пошло не так — нажмите «Помощь», "
        "и мы быстро разберёмся."
    ),
    TrialReminderKind.ending: (
        "Кажется, ваш пробный период скоро закончится 👀\n\n"
        "Если вам понравился BlazeVPN, продлить подписку можно "
        "в личном кабинете."
    ),
    TrialReminderKind.expired: (
        "Ваш пробный период закончился 🫡\n\n"
        "Но мы никуда не делись — вернуться можно в любой момент "
        "всего в пару нажатий."
    ),
}


def trial_reminder_keyboard(kind: TrialReminderKind) -> InlineKeyboardMarkup:
    if kind == TrialReminderKind.connection:
        rows = [
            [
                InlineKeyboardButton(
                    text="🚀 Подключиться",
                    callback_data=START_CONNECTION_CALLBACK,
                ),
                InlineKeyboardButton(
                    text="❓ Помощь",
                    callback_data=HELP_CENTER_CALLBACK,
                ),
            ]
        ]
    elif kind == TrialReminderKind.ending:
        rows = [
            [
                InlineKeyboardButton(
                    text="Продлить подписку",
                    callback_data=TARIFFS_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="👤 Личный кабинет",
                    callback_data=MY_SUBSCRIPTION_CALLBACK,
                )
            ],
        ]
    else:
        rows = [
            [
                InlineKeyboardButton(
                    text="🚀 Вернуть доступ",
                    callback_data=TARIFFS_CALLBACK,
                )
            ]
        ]
    return InlineKeyboardMarkup(inline_keyboard=rows)


def is_delivery_time(moment: datetime) -> bool:
    local = moment.astimezone(MOSCOW)
    return DELIVERY_START_HOUR <= local.hour < DELIVERY_END_HOUR


def reminder_field(kind: TrialReminderKind) -> str:
    return {
        TrialReminderKind.connection: "trial_connection_notice_at",
        TrialReminderKind.ending: "expiry_notice_3d_at",
        TrialReminderKind.expired: "expired_notice_at",
    }[kind]


async def _wait_or_stop(stop_event: asyncio.Event, seconds: float) -> bool:
    try:
        await asyncio.wait_for(stop_event.wait(), timeout=max(seconds, 0))
    except TimeoutError:
        return False
    return True


async def run_trial_reminders(
    *,
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    stop_event: asyncio.Event,
    remnawave_client: RemnawaveClient | None,
    initial_delay_seconds: float = 6 * 60 * 60,
    interval_seconds: float = 15 * 60,
    batch_size: int = 5,
) -> None:
    logger.info(
        "Trial reminders scheduled: initial_delay=%ss interval=%ss batch_size=%s",
        initial_delay_seconds,
        interval_seconds,
        batch_size,
    )
    if await _wait_or_stop(stop_event, initial_delay_seconds):
        return

    while not stop_event.is_set():
        now = datetime.now(UTC)
        if is_delivery_time(now):
            try:
                sent = await send_due_trial_reminders(
                    session_factory,
                    bot,
                    remnawave_client=remnawave_client,
                    now=now,
                    batch_size=batch_size,
                )
                if sent:
                    logger.info("Trial reminder batch sent: %s", sent)
            except Exception:
                logger.exception("Trial reminder batch failed")
        if await _wait_or_stop(stop_event, interval_seconds):
            return


async def send_due_trial_reminders(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    *,
    remnawave_client: RemnawaveClient | None,
    now: datetime,
    batch_size: int,
) -> int:
    sent = 0
    # Current expiring trials take priority over onboarding and historical expiry.
    for kind in (
        TrialReminderKind.ending,
        TrialReminderKind.connection,
        TrialReminderKind.expired,
    ):
        remaining = batch_size - sent
        if remaining <= 0:
            break
        if kind == TrialReminderKind.connection and remnawave_client is None:
            continue
        ids = await _candidate_ids(
            session_factory,
            kind=kind,
            now=now,
            limit=remaining,
        )
        for subscription_id in ids:
            if await _deliver_reminder(
                session_factory,
                bot,
                subscription_id=subscription_id,
                kind=kind,
                now=now,
                remnawave_client=remnawave_client,
            ):
                sent += 1
    return sent


async def _candidate_ids(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    kind: TrialReminderKind,
    now: datetime,
    limit: int,
) -> list[object]:
    conditions = [
        Subscription.source_type == SubscriptionSource.trial,
        User.is_blocked.is_(False),
        getattr(Subscription, reminder_field(kind)).is_(None),
    ]
    if kind == TrialReminderKind.connection:
        conditions.extend(
            [
                Subscription.status == SubscriptionStatus.active,
                Subscription.provisioning_status == ProvisioningStatus.active,
                Subscription.started_at <= now - timedelta(days=2),
                Subscription.expires_at > now + timedelta(days=3),
                Subscription.remnawave_user_uuid.is_not(None),
            ]
        )
        ordering = Subscription.started_at.asc()
    elif kind == TrialReminderKind.ending:
        conditions.extend(
            [
                Subscription.status == SubscriptionStatus.active,
                Subscription.expires_at > now,
                Subscription.expires_at <= now + timedelta(days=3),
            ]
        )
        ordering = Subscription.expires_at.asc()
    else:
        conditions.extend(
            [
                Subscription.status == SubscriptionStatus.expired,
                Subscription.expires_at <= now,
            ]
        )
        ordering = Subscription.expires_at.desc()

    async with session_factory() as session:
        return list(
            await session.scalars(
                select(Subscription.id)
                .join(User, User.id == Subscription.user_id)
                .where(*conditions)
                .order_by(ordering)
                .limit(limit)
            )
        )


def _still_due(
    subscription: Subscription,
    kind: TrialReminderKind,
    now: datetime,
) -> bool:
    if subscription.source_type != SubscriptionSource.trial:
        return False
    if getattr(subscription, reminder_field(kind)) is not None:
        return False
    if kind == TrialReminderKind.connection:
        return (
            subscription.status == SubscriptionStatus.active
            and subscription.provisioning_status == ProvisioningStatus.active
            and subscription.started_at <= now - timedelta(days=2)
            and subscription.expires_at > now + timedelta(days=3)
            and bool(subscription.remnawave_user_uuid)
        )
    if kind == TrialReminderKind.ending:
        return (
            subscription.status == SubscriptionStatus.active
            and now < subscription.expires_at <= now + timedelta(days=3)
        )
    return (
        subscription.status == SubscriptionStatus.expired
        and subscription.expires_at <= now
    )


async def _deliver_reminder(
    session_factory: async_sessionmaker[AsyncSession],
    bot: Bot,
    *,
    subscription_id: object,
    kind: TrialReminderKind,
    now: datetime,
    remnawave_client: RemnawaveClient | None,
) -> bool:
    async with session_factory() as session, session.begin():
        subscription = await session.scalar(
            select(Subscription)
            .where(Subscription.id == subscription_id)
            .with_for_update(skip_locked=True)
        )
        if subscription is None or not _still_due(subscription, kind, now):
            return False
        user = await session.get(User, subscription.user_id)
        if user is None or user.is_blocked:
            return False

        field = reminder_field(kind)
        if kind == TrialReminderKind.connection:
            if remnawave_client is None or not subscription.remnawave_user_uuid:
                return False
            try:
                remote = await remnawave_client.get_user(
                    subscription.remnawave_user_uuid,
                    operation="trial_connection_reminder_check",
                )
            except RemnawaveError:
                logger.exception(
                    "Could not verify trial connection for subscription %s",
                    subscription.id,
                )
                return False
            if remote.user_traffic.first_connected_at is not None:
                setattr(subscription, field, now)
                add_audit_log(
                    session,
                    action="trial_connection_reminder_skipped_connected",
                    entity_type="subscription",
                    entity_id=subscription.id,
                    actor_user_id=user.id,
                    actor_telegram_id=user.telegram_id,
                )
                return False

        try:
            await bot.send_message(
                user.telegram_id,
                TRIAL_REMINDER_TEXTS[kind],
                reply_markup=trial_reminder_keyboard(kind),
            )
        except TelegramForbiddenError:
            # A blocked bot is not retryable; avoid hammering the same account.
            setattr(subscription, field, now)
            add_audit_log(
                session,
                action="trial_reminder_unreachable",
                entity_type="subscription",
                entity_id=subscription.id,
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_id,
                details={"kind": kind.value},
            )
            return False
        except TelegramAPIError:
            logger.exception(
                "Could not send %s trial reminder for subscription %s",
                kind.value,
                subscription.id,
            )
            return False

        setattr(subscription, field, now)
        add_audit_log(
            session,
            action="trial_reminder_sent",
            entity_type="subscription",
            entity_id=subscription.id,
            actor_user_id=user.id,
            actor_telegram_id=user.telegram_id,
            details={"kind": kind.value},
        )
        return True
