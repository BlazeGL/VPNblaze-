import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from decimal import Decimal
from html import escape

from aiogram import Bot
from aiogram.enums import ChatMemberStatus, ChatType, ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from aiogram.types import (
    User as TelegramUser,
)
from redis.asyncio import Redis
from redis.exceptions import LockError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.crypto import SubscriptionUrlCipher
from app.database.models import Subscription, User
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.remnawave.schemas import RemnawaveUser

logger = logging.getLogger(__name__)

SUPPORT_CLOSE_CALLBACK = "support_close"
SUPPORT_TOPIC_ICON_COLOR = 0x8EEE98
SUPPORT_LOCK_TIMEOUT_SECONDS = 300
SUPPORT_LOCK_WAIT_SECONDS = 20


class SupportDeliveryError(RuntimeError):
    """The support message could not be delivered safely."""


class SupportCaseInactive(SupportDeliveryError):
    """The case was closed after the update passed its initial filter."""


class SupportTopicUnavailable(SupportDeliveryError):
    """Telegram reports that the mapped topic no longer accepts messages."""

    def __init__(self, topic_id: int) -> None:
        super().__init__(f"Support topic {topic_id} is unavailable")
        self.topic_id = topic_id


def _is_topic_unavailable(exc: TelegramBadRequest) -> bool:
    error_text = str(exc).lower()
    return any(
        marker in error_text
        for marker in (
            "topic_closed",
            "topic closed",
            "message thread not found",
            "message_thread_not_found",
        )
    )


def _decoded(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        return value.decode()
    return str(value)


class SupportStore:
    """Persistent support case state stored outside the application database."""

    def __init__(self, redis: Redis, support_chat_id: int | None) -> None:
        self.redis = redis
        # Zero is used only to retain the pending mode long enough to return the
        # configured failure message when SUPPORT_GROUP_ID is absent.
        self.support_chat_id = support_chat_id or 0
        self.prefix = f"blazevpn:support:v1:{self.support_chat_id}"

    def _case_key(self, user_id: int) -> str:
        return f"{self.prefix}:user:{user_id}"

    def _topic_key(self, topic_id: int) -> str:
        return f"{self.prefix}:topic:{topic_id}:user"

    def _lock_key(self, user_id: int) -> str:
        return f"{self.prefix}:lock:{user_id}"

    async def get_mode(self, user_id: int) -> str | None:
        return _decoded(await self.redis.hget(self._case_key(user_id), "mode"))

    async def begin(self, user_id: int) -> str:
        """Enter support mode without resetting an already active conversation."""
        case_key = self._case_key(user_id)
        mode = _decoded(await self.redis.hget(case_key, "mode"))
        if mode == "active":
            return mode
        await self.redis.hset(case_key, "mode", "waiting")
        return "waiting"

    async def get_topic(self, user_id: int) -> int | None:
        value = _decoded(
            await self.redis.hget(self._case_key(user_id), "topic_id")
        )
        try:
            return int(value) if value is not None else None
        except ValueError:
            logger.error("Invalid support topic mapping for user %s", user_id)
            return None

    async def get_user_for_topic(self, topic_id: int) -> int | None:
        value = _decoded(await self.redis.get(self._topic_key(topic_id)))
        try:
            return int(value) if value is not None else None
        except ValueError:
            logger.error("Invalid support user mapping for topic %s", topic_id)
            return None

    async def is_card_ready(self, user_id: int) -> bool:
        value = _decoded(
            await self.redis.hget(self._case_key(user_id), "card_ready")
        )
        return value == "1"

    async def bind_topic(self, user_id: int, topic_id: int) -> None:
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.hset(
            self._case_key(user_id),
            mapping={
                "topic_id": str(topic_id),
                "card_ready": "0",
            },
        )
        pipeline.set(self._topic_key(topic_id), str(user_id))
        await pipeline.execute()

    async def ensure_reverse_mapping(self, user_id: int, topic_id: int) -> None:
        await self.redis.set(self._topic_key(topic_id), str(user_id))

    async def mark_card_ready(self, user_id: int) -> None:
        await self.redis.hset(self._case_key(user_id), "card_ready", "1")

    async def complete_delivery(self, user_id: int) -> bool:
        """Mark the case active and claim the one-time confirmation."""
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.hset(self._case_key(user_id), "mode", "active")
        pipeline.hsetnx(self._case_key(user_id), "confirmed", "1")
        results = await pipeline.execute()
        return bool(results[-1])

    async def clear_case(
        self,
        user_id: int,
        *,
        expected_topic_id: int | None = None,
    ) -> bool:
        current_topic_id = await self.get_topic(user_id)
        if (
            expected_topic_id is not None
            and current_topic_id != expected_topic_id
        ):
            return False
        pipeline = self.redis.pipeline(transaction=True)
        pipeline.delete(self._case_key(user_id))
        topic_id = expected_topic_id or current_topic_id
        if topic_id is not None:
            pipeline.delete(self._topic_key(topic_id))
        await pipeline.execute()
        return True

    @asynccontextmanager
    async def user_lock(self, user_id: int) -> AsyncIterator[None]:
        lock = self.redis.lock(
            self._lock_key(user_id),
            timeout=SUPPORT_LOCK_TIMEOUT_SECONDS,
            blocking_timeout=SUPPORT_LOCK_WAIT_SECONDS,
        )
        acquired = await lock.acquire()
        if not acquired:
            raise SupportDeliveryError("Timed out waiting for support case lock")
        try:
            yield
        finally:
            try:
                await lock.release()
            except LockError:
                logger.warning(
                    "Support case lock expired before release for user %s",
                    user_id,
                )


def support_close_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Закрыть обращение",
                    callback_data=SUPPORT_CLOSE_CALLBACK,
                )
            ]
        ]
    )


def build_topic_name(telegram_user: TelegramUser) -> str:
    full_name = " ".join(telegram_user.full_name.split()) or "Пользователь"
    suffix = f" | {telegram_user.id}"
    prefix = "🟢 "
    available = max(1, 128 - len(prefix) - len(suffix))
    return f"{prefix}{full_name[:available]}{suffix}"


def _money(value: Decimal | None) -> str:
    if value is None:
        return "—"
    return f"{value:.2f}".rstrip("0").rstrip(".").replace(".", ",")


def _bytes(value: int | None, *, unlimited_when_zero: bool = False) -> str:
    if value is None:
        return "—"
    if value == 0 and unlimited_when_zero:
        return "Без лимита"
    units = ("B", "KB", "MB", "GB", "TB", "PB")
    number = float(max(value, 0))
    unit = units[0]
    for candidate in units:
        unit = candidate
        if number < 1024 or candidate == units[-1]:
            break
        number /= 1024
    rendered = f"{number:.2f}".rstrip("0").rstrip(".")
    return f"{rendered} {unit}"


def _date(value: datetime | None, *, with_time: bool = False) -> str:
    if value is None:
        return "—"
    if value.tzinfo is None or value.utcoffset() is None:
        normalized = value.replace(tzinfo=UTC)
    else:
        normalized = value.astimezone(UTC)
    return normalized.strftime("%d.%m.%Y %H:%M UTC" if with_time else "%d.%m.%Y")


def _plain(value: object | None) -> str:
    if value is None or value == "":
        return "—"
    candidate = getattr(value, "value", value)
    return escape(str(candidate))


def build_user_card(
    telegram_user: TelegramUser,
    user: User | None,
    subscription: Subscription | None,
    *,
    remote: RemnawaveUser | None = None,
    subscription_url: str | None = None,
) -> str:
    name = telegram_user.full_name or (
        " ".join(
            item
            for item in (
                user.first_name if user else None,
                user.last_name if user else None,
            )
            if item
        )
        or "Пользователь"
    )
    username = telegram_user.username or (user.username if user else None)

    remote_traffic = remote.user_traffic if remote is not None else None
    remnawave_username = (
        remote.username
        if remote is not None
        else subscription.remnawave_username if subscription is not None else None
    )
    status = (
        remote.status
        if remote is not None
        else (
            subscription.remnawave_status or subscription.status
            if subscription is not None
            else None
        )
    )
    expires_at = (
        remote.expire_at
        if remote is not None
        else subscription.expires_at if subscription is not None else None
    )
    used_traffic = (
        remote_traffic.used_traffic_bytes
        if remote_traffic is not None
        else subscription.used_traffic_bytes if subscription is not None else None
    )
    lifetime_traffic = (
        remote_traffic.lifetime_used_traffic_bytes
        if remote_traffic is not None
        else None
    )
    traffic_limit = (
        remote.traffic_limit_bytes
        if remote is not None
        else (
            subscription.remnawave_traffic_limit_bytes
            if subscription is not None
            else None
        )
    )
    if (
        traffic_limit is None
        and subscription is not None
        and subscription.traffic_limit_gb is not None
    ):
        traffic_limit = subscription.traffic_limit_gb * 1024**3
    traffic_strategy = (
        remote.traffic_limit_strategy if remote is not None else None
    )
    last_reset_at = remote.last_traffic_reset_at if remote is not None else None
    online_at = remote_traffic.online_at if remote_traffic is not None else None
    first_connected_at = (
        remote_traffic.first_connected_at if remote_traffic is not None else None
    )
    device_limit = (
        remote.hwid_device_limit
        if remote is not None
        else subscription.device_limit if subscription is not None else None
    )
    active_devices = (
        subscription.connected_devices if subscription is not None else None
    )

    return (
        "📊 <b>Обращение от пользователя</b>\n\n"
        "👤 <b>Имя:</b>\n"
        f"{escape(name)}\n\n"
        "🔗 <b>Username:</b>\n"
        f"{('@' + escape(username)) if username else '—'}\n\n"
        "🆔 <b>Telegram ID:</b>\n"
        f"<code>{telegram_user.id}</code>\n\n"
        "────────────────────\n\n"
        "💳 <b>Баланс:</b>\n"
        f"{_money(user.balance if user else None)} ₽\n\n"
        "👥 <b>Реферальный баланс:</b>\n"
        f"{_money(user.total_referral_income if user else None)} ₽\n\n"
        "🔑 <b>Ключ-подписка:</b>\n"
        f"{f'<code>{escape(subscription_url)}</code>' if subscription_url else '—'}"
        "\n\n"
        "────────────────────\n\n"
        "👤 <b>Карточка пользователя</b>\n\n"
        "<b>Username:</b>\n"
        f"{_plain(remnawave_username)}\n\n"
        "<b>Статус:</b>\n"
        f"{_plain(status)}\n\n"
        "────────────────────\n\n"
        "📦 <b>Подписка</b>\n\n"
        "<b>Истекает:</b>\n"
        f"{_date(expires_at)}\n\n"
        "────────────────────\n\n"
        "📊 <b>Трафик</b>\n\n"
        "<b>Использовано после сброса:</b>\n"
        f"{_bytes(used_traffic)}\n\n"
        "<b>Всего:</b>\n"
        f"{_bytes(lifetime_traffic)}\n\n"
        "<b>Лимит:</b>\n"
        f"{_bytes(traffic_limit, unlimited_when_zero=True)}\n\n"
        "<b>Сброс:</b>\n"
        f"{_plain(traffic_strategy)}\n\n"
        "<b>Последний сброс:</b>\n"
        f"{_date(last_reset_at)}\n\n"
        "────────────────────\n\n"
        "🕒 <b>Активность</b>\n\n"
        "<b>Последний онлайн:</b>\n"
        f"{_date(online_at, with_time=True)}\n\n"
        "<b>Первое подключение:</b>\n"
        f"{_date(first_connected_at, with_time=True)}\n\n"
        "────────────────────\n\n"
        "🔐 <b>Ограничения</b>\n\n"
        "<b>Лимит устройств:</b>\n"
        f"{_plain(device_limit)}\n\n"
        "<b>Активно:</b>\n"
        f"{_plain(active_devices)}\n\n"
        "────────────────────\n\n"
        "👥 <b>Рефералов:</b>\n"
        f"{user.total_referrals if user else 0}"
    )


async def load_user_card(
    telegram_user: TelegramUser,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None,
    subscription_cipher: SubscriptionUrlCipher | None,
) -> str:
    async with session_factory() as session:
        row = (
            await session.execute(
                select(User, Subscription)
                .outerjoin(Subscription, Subscription.user_id == User.id)
                .where(User.telegram_id == telegram_user.id)
            )
        ).one_or_none()
    user: User | None
    subscription: Subscription | None
    if row is None:
        user, subscription = None, None
    else:
        user, subscription = row

    remote: RemnawaveUser | None = None
    if remnawave_client is not None and subscription is not None:
        try:
            if subscription.remnawave_user_uuid:
                remote = await remnawave_client.get_user(
                    subscription.remnawave_user_uuid,
                    operation="support_user_card",
                    local_user_id=user.id if user is not None else None,
                    remnawave_username=subscription.remnawave_username,
                )
            elif subscription.remnawave_username:
                remote = await remnawave_client.get_user_by_username(
                    subscription.remnawave_username,
                    local_user_id=user.id if user is not None else None,
                )
        except Exception:
            logger.exception(
                "Could not load current Remnawave data for support user %s",
                telegram_user.id,
            )

    subscription_url = remote.subscription_url if remote is not None else None
    if (
        subscription_url is None
        and subscription is not None
        and subscription.subscription_url_encrypted
        and subscription_cipher is not None
    ):
        try:
            subscription_url = subscription_cipher.decrypt(
                subscription.subscription_url_encrypted
            )
        except (TypeError, ValueError):
            logger.exception(
                "Could not decrypt subscription URL for support user %s",
                telegram_user.id,
            )

    return build_user_card(
        telegram_user,
        user,
        subscription,
        remote=remote,
        subscription_url=subscription_url,
    )


async def deliver_user_message(
    message: Message,
    *,
    support_chat_id: int,
    store: SupportStore,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None,
    subscription_cipher: SubscriptionUrlCipher | None,
) -> bool:
    telegram_user = message.from_user
    if telegram_user is None:
        raise SupportDeliveryError("Support message has no Telegram user")

    card: str | None = None
    topic_id: int | None = None
    try:
        async with store.user_lock(telegram_user.id):
            if await store.get_mode(telegram_user.id) not in {
                "waiting",
                "active",
            }:
                raise SupportCaseInactive("Support case is no longer active")
            topic_id = await store.get_topic(telegram_user.id)
            if topic_id is None:
                topic = await message.bot.create_forum_topic(
                    chat_id=support_chat_id,
                    name=build_topic_name(telegram_user),
                    icon_color=SUPPORT_TOPIC_ICON_COLOR,
                )
                topic_id = topic.message_thread_id
                try:
                    await store.bind_topic(telegram_user.id, topic_id)
                except Exception:
                    try:
                        await message.bot.close_forum_topic(
                            chat_id=support_chat_id,
                            message_thread_id=topic_id,
                        )
                    except Exception:
                        logger.exception(
                            "Could not close orphan support topic %s",
                            topic_id,
                        )
                    raise
            else:
                await store.ensure_reverse_mapping(telegram_user.id, topic_id)

            if not await store.is_card_ready(telegram_user.id):
                if card is None:
                    card = await load_user_card(
                        telegram_user,
                        session_factory,
                        remnawave_client,
                        subscription_cipher,
                    )
                await message.bot.send_message(
                    chat_id=support_chat_id,
                    message_thread_id=topic_id,
                    text=card,
                    reply_markup=support_close_keyboard(),
                    parse_mode=ParseMode.HTML,
                )
                await store.mark_card_ready(telegram_user.id)

            header = await message.bot.send_message(
                chat_id=support_chat_id,
                message_thread_id=topic_id,
                text="<b>👤 Пользователь:</b>",
                parse_mode=ParseMode.HTML,
            )
            try:
                await message.copy_to(
                    chat_id=support_chat_id,
                    message_thread_id=topic_id,
                )
            except Exception:
                try:
                    await message.bot.delete_message(
                        chat_id=support_chat_id,
                        message_id=header.message_id,
                    )
                except Exception:
                    logger.debug(
                        "Could not remove orphan support header in topic %s",
                        topic_id,
                        exc_info=True,
                    )
                raise

            return await store.complete_delivery(telegram_user.id)
    except TelegramBadRequest as exc:
        if topic_id is not None and _is_topic_unavailable(exc):
            raise SupportTopicUnavailable(topic_id) from exc
        raise


async def validate_support_group(
    bot: Bot,
    support_chat_id: int | None,
) -> bool:
    if support_chat_id is None:
        logger.error(
            "SUPPORT_GROUP_ID is not configured; support delivery is disabled"
        )
        return False
    try:
        chat = await bot.get_chat(support_chat_id)
        if chat.type != ChatType.SUPERGROUP or not chat.is_forum:
            logger.error(
                "Support chat %s must be a forum supergroup",
                support_chat_id,
            )
            return False
        identity = await bot.get_me()
        member = await bot.get_chat_member(support_chat_id, identity.id)
        is_owner = member.status == ChatMemberStatus.CREATOR
        can_manage_topics = bool(getattr(member, "can_manage_topics", False))
        if not is_owner and (
            member.status != ChatMemberStatus.ADMINISTRATOR
            or not can_manage_topics
        ):
            logger.error(
                "Bot needs administrator can_manage_topics permission in %s",
                support_chat_id,
            )
            return False
    except Exception:
        logger.exception("Could not validate support group %s", support_chat_id)
        return False
    logger.info("Telegram support forum %s validated", support_chat_id)
    return True
