import logging

from aiogram import F, Router
from aiogram.enums import (
    ChatMemberStatus,
    ChatType,
    ContentType,
    MessageEntityType,
)
from aiogram.exceptions import TelegramBadRequest
from aiogram.filters import BaseFilter, Command
from aiogram.fsm.context import FSMContext
from aiogram.types import CallbackQuery, Message, MessageEntity
from redis.asyncio import Redis
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.services.support import (
    SUPPORT_CLOSE_CALLBACK,
    SupportStore,
    SupportTopicUnavailable,
    deliver_user_message,
)
from app.core.config import Settings
from app.core.crypto import SubscriptionUrlCipher
from app.integrations.remnawave.client import RemnawaveClient

logger = logging.getLogger(__name__)

control_router = Router(name=f"{__name__}.control")
user_router = Router(name=f"{__name__}.user")

SUPPORT_ENTRY_CALLBACKS = {
    "support_from_key",
    "support_from_main",
    "support_from_subscription",
}

SUPPORT_PROMPT = (
    "🛟 Служба заботы BlazeVPN\n\n"
    "Пожалуйста, подробно опишите свою проблему прямо здесь, в боте.\n\n"
    "Укажите:\n"
    "• что именно не работает;\n"
    "• на каком устройстве возникла проблема;\n"
    "• каким приложением для VPN вы пользуетесь;\n"
    "• когда появилась ошибка.\n\n"
    "При необходимости приложите скриншот или видео."
)

SUPPORT_CONFIRMATION = (
    "Ваше сообщение передано в службу заботы, ожидайте ответа.\n\n"
    "Получить ключ 👉 /key\n"
    "Пополнить/проверить баланс 👉 /balance"
)

SUPPORT_DELIVERY_FAILED = (
    "Не удалось передать сообщение в службу заботы. "
    "Пожалуйста, попробуйте ещё раз немного позже."
)

ADMIN_DELIVERY_FAILED = (
    "⚠️ Не удалось доставить ответ пользователю. "
    "Попробуйте ещё раз немного позже."
)

ADMIN_COPYABLE_CONTENT = {
    ContentType.TEXT,
    ContentType.ANIMATION,
    ContentType.AUDIO,
    ContentType.CONTACT,
    ContentType.DICE,
    ContentType.DOCUMENT,
    ContentType.LOCATION,
    ContentType.PHOTO,
    ContentType.POLL,
    ContentType.STICKER,
    ContentType.VENUE,
    ContentType.VIDEO,
    ContentType.VIDEO_NOTE,
    ContentType.VOICE,
}


def _has_bot_command(message: Message) -> bool:
    for entity in (
        *(message.entities or []),
        *(message.caption_entities or []),
    ):
        if entity.type == MessageEntityType.BOT_COMMAND and entity.offset == 0:
            return True
    text = message.text or message.caption
    return bool(text and text.lstrip().startswith("/"))


def _confirmation_entities() -> list[MessageEntity]:
    entities: list[MessageEntity] = []
    for command in ("/key", "/balance"):
        python_offset = SUPPORT_CONFIRMATION.index(command)
        utf16_offset = len(
            SUPPORT_CONFIRMATION[:python_offset].encode("utf-16-le")
        ) // 2
        utf16_length = len(command.encode("utf-16-le")) // 2
        entities.append(
            MessageEntity(
                type=MessageEntityType.BOT_COMMAND,
                offset=utf16_offset,
                length=utf16_length,
            )
        )
    return entities


async def _is_support_admin(
    message: Message,
    *,
    support_chat_id: int,
    admin_ids: set[int],
    user_id: int | None = None,
) -> bool:
    if (
        user_id is None
        and message.sender_chat is not None
        and message.sender_chat.id == support_chat_id
    ):
        return True
    candidate_id = user_id or (
        message.from_user.id if message.from_user is not None else None
    )
    if candidate_id is None:
        return False
    if candidate_id in admin_ids:
        return True
    try:
        member = await message.bot.get_chat_member(
            chat_id=support_chat_id,
            user_id=candidate_id,
        )
    except Exception:
        logger.exception(
            "Could not verify support administrator %s",
            candidate_id,
        )
        return False
    return member.status in {
        ChatMemberStatus.CREATOR,
        ChatMemberStatus.ADMINISTRATOR,
    }


class SupportModeFilter(BaseFilter):
    async def __call__(
        self,
        message: Message,
        redis_client: Redis,
        settings: Settings,
    ) -> bool:
        if (
            message.chat.type != ChatType.PRIVATE
            or message.from_user is None
            or _has_bot_command(message)
        ):
            return False
        try:
            store = SupportStore(
                redis_client,
                settings.support_group_id,
            )
            mode = await store.get_mode(message.from_user.id)
            if mode is None and settings.support_group_id is not None:
                legacy_mode = await SupportStore(
                    redis_client,
                    None,
                ).get_mode(message.from_user.id)
                if legacy_mode in {"waiting", "active"}:
                    mode = await store.begin(message.from_user.id)
                    logger.info(
                        "Restored pending support mode for user %s after support "
                        "group configuration",
                        message.from_user.id,
                    )
        except Exception:
            logger.exception(
                "Could not read support mode for user %s",
                message.from_user.id,
            )
            return False
        return mode in {"waiting", "active"}


class SupportAdminMessageFilter(BaseFilter):
    async def __call__(
        self,
        message: Message,
        redis_client: Redis,
        settings: Settings,
        admin_ids: set[int],
    ) -> bool | dict[str, int]:
        support_chat_id = settings.support_group_id
        topic_id = message.message_thread_id
        if (
            support_chat_id is None
            or message.chat.id != support_chat_id
            or topic_id is None
            or message.content_type not in ADMIN_COPYABLE_CONTENT
            or _has_bot_command(message)
        ):
            return False
        if (
            message.from_user is not None
            and message.from_user.is_bot
            and not (
                message.sender_chat is not None
                and message.sender_chat.id == support_chat_id
            )
        ):
            return False
        if not await _is_support_admin(
            message,
            support_chat_id=support_chat_id,
            admin_ids=admin_ids,
        ):
            return False
        user_id = await SupportStore(
            redis_client,
            support_chat_id,
        ).get_user_for_topic(topic_id)
        if user_id is None:
            return False
        return {"support_user_id": user_id}


async def begin_support(
    message: Message,
    *,
    state: FSMContext,
    redis_client: Redis,
    settings: Settings,
) -> bool:
    if message.chat.type != ChatType.PRIVATE:
        return False
    # For callback queries ``message`` is the bot's own menu message, so
    # ``message.from_user`` points to the bot.  A private chat ID always points
    # to the human participant and works for both callbacks and /support.
    support_user_id = message.chat.id
    try:
        await state.clear()
        store = SupportStore(
            redis_client,
            settings.support_group_id,
        )
        async with store.user_lock(support_user_id):
            await store.begin(support_user_id)
    except Exception:
        logger.exception(
            "Could not activate support mode for user %s",
            support_user_id,
        )
        await message.answer(SUPPORT_DELIVERY_FAILED)
        return False
    logger.info("Activated support mode for user %s", support_user_id)
    await message.answer(SUPPORT_PROMPT)
    return True


@control_router.message(Command(commands=["support_chat_id", "id"]))
async def show_support_chat_id(
    message: Message,
    admin_ids: set[int],
) -> None:
    if (
        message.chat.type != ChatType.SUPERGROUP
        or not message.chat.is_forum
        or not await _is_support_admin(
            message,
            support_chat_id=message.chat.id,
            admin_ids=admin_ids,
        )
    ):
        return
    logger.info(
        "Forum chat ID requested by administrator: %s",
        message.chat.id,
    )
    await message.answer(
        f"🆔 ID forum-группы: <code>{message.chat.id}</code>",
        parse_mode="HTML",
    )


@control_router.callback_query(F.data.in_(SUPPORT_ENTRY_CALLBACKS))
async def begin_support_callback(
    callback: CallbackQuery,
    state: FSMContext,
    redis_client: Redis,
    settings: Settings,
) -> None:
    if not isinstance(callback.message, Message):
        await callback.answer("Сообщение больше недоступно.", show_alert=True)
        return
    if callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Откройте бота в личных сообщениях.",
            show_alert=True,
        )
        return
    await callback.answer()
    await begin_support(
        callback.message,
        state=state,
        redis_client=redis_client,
        settings=settings,
    )


@control_router.callback_query(F.data == SUPPORT_CLOSE_CALLBACK)
async def close_support_case(
    callback: CallbackQuery,
    redis_client: Redis,
    settings: Settings,
    admin_ids: set[int],
) -> None:
    message = callback.message
    support_chat_id = settings.support_group_id
    if (
        not isinstance(message, Message)
        or support_chat_id is None
        or message.chat.id != support_chat_id
        or message.message_thread_id is None
    ):
        await callback.answer("Обращение не найдено.", show_alert=True)
        return
    if not await _is_support_admin(
        message,
        support_chat_id=support_chat_id,
        admin_ids=admin_ids,
        user_id=callback.from_user.id,
    ):
        await callback.answer(
            "Закрыть обращение может только администратор.",
            show_alert=True,
        )
        return

    store = SupportStore(redis_client, support_chat_id)
    topic_id = message.message_thread_id
    user_id = await store.get_user_for_topic(topic_id)
    if user_id is None:
        await callback.answer("Обращение уже закрыто.", show_alert=True)
        return
    try:
        async with store.user_lock(user_id):
            if (
                await store.get_user_for_topic(topic_id) != user_id
                or await store.get_topic(user_id) != topic_id
            ):
                await callback.answer(
                    "Обращение уже закрыто.",
                    show_alert=True,
                )
                return
            await callback.bot.close_forum_topic(
                chat_id=support_chat_id,
                message_thread_id=topic_id,
            )
            await store.clear_case(user_id, expected_topic_id=topic_id)
    except Exception:
        logger.exception(
            "Could not close support topic %s for user %s",
            topic_id,
            user_id,
        )
        await callback.answer(
            "Не удалось закрыть обращение.",
            show_alert=True,
        )
        return
    await callback.answer("Обращение закрыто.")


@control_router.message(F.forum_topic_closed)
async def handle_forum_topic_closed(
    message: Message,
    redis_client: Redis,
    settings: Settings,
) -> None:
    support_chat_id = settings.support_group_id
    topic_id = message.message_thread_id
    if (
        support_chat_id is None
        or message.chat.id != support_chat_id
        or topic_id is None
    ):
        return
    store = SupportStore(redis_client, support_chat_id)
    user_id = await store.get_user_for_topic(topic_id)
    if user_id is not None:
        async with store.user_lock(user_id):
            if await store.get_user_for_topic(topic_id) == user_id:
                await store.clear_case(
                    user_id,
                    expected_topic_id=topic_id,
                )


@control_router.message(SupportAdminMessageFilter())
async def relay_admin_message(
    message: Message,
    support_user_id: int,
) -> None:
    try:
        await message.copy_to(chat_id=support_user_id)
    except Exception:
        logger.exception(
            "Could not deliver support reply from topic %s to user %s",
            message.message_thread_id,
            support_user_id,
        )
        await message.bot.send_message(
            chat_id=message.chat.id,
            message_thread_id=message.message_thread_id,
            text=ADMIN_DELIVERY_FAILED,
        )


@user_router.message(SupportModeFilter())
async def relay_user_message(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    redis_client: Redis,
    settings: Settings,
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
) -> None:
    support_chat_id = settings.support_group_id
    if support_chat_id is None:
        await message.answer(SUPPORT_DELIVERY_FAILED)
        return
    store = SupportStore(redis_client, support_chat_id)
    try:
        should_confirm = await deliver_user_message(
            message,
            support_chat_id=support_chat_id,
            store=store,
            session_factory=session_factory,
            remnawave_client=remnawave_client,
            subscription_cipher=subscription_cipher,
        )
    except SupportTopicUnavailable as exc:
        if message.from_user is not None:
            try:
                async with store.user_lock(message.from_user.id):
                    await store.clear_case(
                        message.from_user.id,
                        expected_topic_id=exc.topic_id,
                    )
            except Exception:
                logger.exception(
                    "Could not clear unavailable support topic %s for user %s",
                    exc.topic_id,
                    message.from_user.id,
                )
        logger.exception(
            "Telegram support topic %s is unavailable for user %s",
            exc.topic_id,
            message.from_user.id if message.from_user else None,
        )
        await message.answer(SUPPORT_DELIVERY_FAILED)
        return
    except TelegramBadRequest:
        logger.exception(
            "Telegram rejected a support message for user %s",
            message.from_user.id if message.from_user else None,
        )
        await message.answer(SUPPORT_DELIVERY_FAILED)
        return
    except Exception:
        logger.exception(
            "Could not deliver support message for user %s",
            message.from_user.id if message.from_user else None,
        )
        await message.answer(SUPPORT_DELIVERY_FAILED)
        return

    if should_confirm:
        await message.answer(
            SUPPORT_CONFIRMATION,
            entities=_confirmation_entities(),
        )
