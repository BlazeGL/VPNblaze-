import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from aiogram.enums import ChatType, ContentType, MessageEntityType
from aiogram.types import Chat, Message, MessageEntity
from aiogram.types import User as TelegramUser

from app.bot.handlers.support import (
    SUPPORT_CONFIRMATION,
    SUPPORT_DELIVERY_FAILED,
    SUPPORT_PROMPT,
    SupportAdminMessageFilter,
    SupportModeFilter,
    _confirmation_entities,
    begin_support,
    close_support_case,
    relay_admin_message,
    relay_user_message,
    show_support_chat_id,
)
from app.bot.services import support as support_service
from app.bot.services.support import (
    SUPPORT_TOPIC_ICON_COLOR,
    SupportStore,
    build_topic_name,
    build_user_card,
)

SUPPORT_CHAT_ID = -1001234567890
USER_ID = 5104324589
BOT_ID = 8633357281
ADMIN_ID = 123456789
TOPIC_ID = 731

EXPECTED_PROMPT = (
    "🛟 Служба заботы BlazeVPN\n\n"
    "Пожалуйста, подробно опишите свою проблему прямо здесь, в боте.\n\n"
    "Укажите:\n"
    "• что именно не работает;\n"
    "• на каком устройстве возникла проблема;\n"
    "• каким приложением для VPN вы пользуетесь;\n"
    "• когда появилась ошибка.\n\n"
    "При необходимости приложите скриншот или видео."
)

EXPECTED_CONFIRMATION = (
    "Ваше сообщение передано в службу заботы, ожидайте ответа.\n\n"
    "Получить ключ 👉 /key\n"
    "Пополнить/проверить баланс 👉 /balance"
)


class FakePipeline:
    def __init__(self, redis: "FakeRedis") -> None:
        self.redis = redis
        self.operations: list[Callable[[], Awaitable[object]]] = []

    def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        *,
        mapping: dict[str, str] | None = None,
    ) -> "FakePipeline":
        async def operation() -> object:
            return await self.redis.hset(
                name,
                key,
                value,
                mapping=mapping,
            )

        self.operations.append(operation)
        return self

    def hsetnx(self, name: str, key: str, value: str) -> "FakePipeline":
        async def operation() -> object:
            return await self.redis.hsetnx(name, key, value)

        self.operations.append(operation)
        return self

    def set(self, name: str, value: str) -> "FakePipeline":
        async def operation() -> object:
            return await self.redis.set(name, value)

        self.operations.append(operation)
        return self

    def delete(self, *names: str) -> "FakePipeline":
        async def operation() -> object:
            return await self.redis.delete(*names)

        self.operations.append(operation)
        return self

    async def execute(self) -> list[object]:
        return [await operation() for operation in self.operations]


class FakeLock:
    def __init__(self, lock: asyncio.Lock) -> None:
        self.lock = lock

    async def acquire(self) -> bool:
        await self.lock.acquire()
        return True

    async def release(self) -> None:
        self.lock.release()


class FakeRedis:
    def __init__(self) -> None:
        self.hashes: dict[str, dict[str, str]] = {}
        self.values: dict[str, str] = {}
        self.locks: dict[str, asyncio.Lock] = {}

    async def hget(self, name: str, key: str) -> str | None:
        return self.hashes.get(name, {}).get(key)

    async def hset(
        self,
        name: str,
        key: str | None = None,
        value: str | None = None,
        *,
        mapping: dict[str, str] | None = None,
    ) -> int:
        target = self.hashes.setdefault(name, {})
        changed = 0
        if mapping is not None:
            for field, field_value in mapping.items():
                changed += int(field not in target)
                target[field] = str(field_value)
        if key is not None:
            changed += int(key not in target)
            target[key] = "" if value is None else str(value)
        return changed

    async def hsetnx(self, name: str, key: str, value: str) -> int:
        target = self.hashes.setdefault(name, {})
        if key in target:
            return 0
        target[key] = str(value)
        return 1

    async def get(self, name: str) -> str | None:
        return self.values.get(name)

    async def set(self, name: str, value: str) -> bool:
        self.values[name] = str(value)
        return True

    async def delete(self, *names: str) -> int:
        deleted = 0
        for name in names:
            deleted += int(self.hashes.pop(name, None) is not None)
            deleted += int(self.values.pop(name, None) is not None)
        return deleted

    def pipeline(self, *, transaction: bool) -> FakePipeline:
        assert transaction is True
        return FakePipeline(self)

    def lock(
        self,
        name: str,
        *,
        timeout: int,
        blocking_timeout: int,
    ) -> FakeLock:
        assert name
        assert timeout > 0
        assert blocking_timeout > 0
        return FakeLock(self.locks.setdefault(name, asyncio.Lock()))


def telegram_user(
    *,
    user_id: int = USER_ID,
    first_name: str = "Татьяна",
    last_name: str = "Парфенова",
    username: str | None = "Parfenova12",
    is_bot: bool = False,
) -> TelegramUser:
    return TelegramUser(
        id=user_id,
        is_bot=is_bot,
        first_name=first_name,
        last_name=last_name,
        username=username,
    )


def fake_bot(*, topic_id: int = TOPIC_ID) -> SimpleNamespace:
    return SimpleNamespace(
        create_forum_topic=AsyncMock(
            return_value=SimpleNamespace(message_thread_id=topic_id)
        ),
        close_forum_topic=AsyncMock(),
        send_message=AsyncMock(return_value=SimpleNamespace(message_id=900)),
        delete_message=AsyncMock(),
        get_chat_member=AsyncMock(),
    )


def private_message(
    bot: SimpleNamespace,
    user: TelegramUser,
    *,
    text: str = "Не работает VPN",
    chat_id: int | None = None,
) -> SimpleNamespace:
    return SimpleNamespace(
        bot=bot,
        chat=SimpleNamespace(id=chat_id or user.id, type=ChatType.PRIVATE),
        from_user=user,
        text=text,
        caption=None,
        entities=None,
        caption_entities=None,
        content_type=ContentType.TEXT,
        copy_to=AsyncMock(),
        answer=AsyncMock(),
    )


@pytest.mark.asyncio
async def test_support_store_maps_confirms_once_and_clears_both_directions() -> None:
    redis = FakeRedis()
    store = SupportStore(redis, SUPPORT_CHAT_ID)  # type: ignore[arg-type]

    assert await store.begin(USER_ID) == "waiting"
    await store.bind_topic(USER_ID, TOPIC_ID)

    assert await store.get_mode(USER_ID) == "waiting"
    assert await store.get_topic(USER_ID) == TOPIC_ID
    assert await store.get_user_for_topic(TOPIC_ID) == USER_ID
    assert await store.is_card_ready(USER_ID) is False

    await store.mark_card_ready(USER_ID)
    assert await store.is_card_ready(USER_ID) is True
    assert await store.complete_delivery(USER_ID) is True
    assert await store.complete_delivery(USER_ID) is False
    assert await store.get_mode(USER_ID) == "active"

    assert await store.clear_case(USER_ID, expected_topic_id=999) is False
    assert await store.get_topic(USER_ID) == TOPIC_ID

    assert await store.clear_case(USER_ID, expected_topic_id=TOPIC_ID) is True
    assert await store.get_mode(USER_ID) is None
    assert await store.get_topic(USER_ID) is None
    assert await store.get_user_for_topic(TOPIC_ID) is None


@pytest.mark.asyncio
async def test_begin_support_uses_exact_prompt_and_confirmation_is_exact() -> None:
    assert SUPPORT_PROMPT == EXPECTED_PROMPT
    assert SUPPORT_CONFIRMATION == EXPECTED_CONFIRMATION

    redis = FakeRedis()
    user = telegram_user()
    message = private_message(fake_bot(), user)
    state = SimpleNamespace(clear=AsyncMock())
    settings = SimpleNamespace(support_group_id=SUPPORT_CHAT_ID)

    assert await begin_support(
        message,
        state=state,
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    state.clear.assert_awaited_once_with()
    message.answer.assert_awaited_once_with(EXPECTED_PROMPT)
    assert (
        await SupportStore(  # type: ignore[arg-type]
            redis,
            SUPPORT_CHAT_ID,
        ).get_mode(USER_ID)
        == "waiting"
    )

    entities = _confirmation_entities()
    assert [entity.type for entity in entities] == [
        MessageEntityType.BOT_COMMAND,
        MessageEntityType.BOT_COMMAND,
    ]
    assert [
        SUPPORT_CONFIRMATION.encode("utf-16-le")[
            entity.offset * 2 : (entity.offset + entity.length) * 2
        ].decode("utf-16-le")
        for entity in entities
    ] == ["/key", "/balance"]


@pytest.mark.asyncio
async def test_begin_support_from_bot_menu_uses_private_chat_user_id() -> None:
    redis = FakeRedis()
    bot_sender = telegram_user(user_id=BOT_ID, first_name="BlazeVPN", is_bot=True)
    message = private_message(fake_bot(), bot_sender, chat_id=USER_ID)
    state = SimpleNamespace(clear=AsyncMock())
    settings = SimpleNamespace(support_group_id=SUPPORT_CHAT_ID)

    assert await begin_support(
        message,
        state=state,
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )

    store = SupportStore(redis, SUPPORT_CHAT_ID)  # type: ignore[arg-type]
    assert await store.get_mode(USER_ID) == "waiting"
    assert await store.get_mode(BOT_ID) is None


def test_topic_title_is_truncated_to_telegram_limit_and_keeps_user_id() -> None:
    user = telegram_user(first_name="ОченьДлинноеИмя" * 20, last_name="Фамилия" * 20)

    title = build_topic_name(user)

    assert len(title) == 128
    assert title.startswith("🟢 ")
    assert title.endswith(f" | {USER_ID}")


def test_user_card_escapes_values_and_falls_back_to_local_data() -> None:
    user = telegram_user(
        first_name="<Татьяна>",
        last_name="& Парфенова",
        username="name<admin>",
    )
    local_user = SimpleNamespace(
        first_name="<Локальное>",
        last_name="& имя",
        username="local<name>",
        balance=Decimal("1130"),
        total_referral_income=Decimal("20"),
        total_referrals=3,
    )
    subscription = SimpleNamespace(
        remnawave_username="vles_<unsafe>&",
        remnawave_status="ACTIVE<&>",
        status="active",
        expires_at=None,
        used_traffic_bytes=None,
        remnawave_traffic_limit_bytes=None,
        traffic_limit_gb=600,
        device_limit=5,
        connected_devices=4,
    )

    card = build_user_card(
        user,
        local_user,  # type: ignore[arg-type]
        subscription,  # type: ignore[arg-type]
        remote=None,
        subscription_url="https://key.example/<secret>?x=1&y=2",
    )

    assert "&lt;Татьяна&gt; &amp; Парфенова" in card
    assert "@name&lt;admin&gt;" in card
    assert "vles_&lt;unsafe&gt;&amp;" in card
    assert "ACTIVE&lt;&amp;&gt;" in card
    assert "https://key.example/&lt;secret&gt;?x=1&amp;y=2" in card
    assert "<Татьяна>" not in card
    assert "vles_<unsafe>" not in card

    fallback = build_user_card(user, None, None)
    assert f"<code>{USER_ID}</code>" in fallback
    assert "—" in fallback


@pytest.mark.asyncio
async def test_first_and_repeated_messages_reuse_one_topic_card_and_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    store = SupportStore(redis, SUPPORT_CHAT_ID)  # type: ignore[arg-type]
    await store.begin(USER_ID)
    bot = fake_bot()
    user = telegram_user()
    first = private_message(bot, user, text="Здравствуйте")
    repeated = private_message(bot, user, text="Не работает VPN")
    load_card = AsyncMock(return_value="<b>USER CARD</b>")
    monkeypatch.setattr(support_service, "load_user_card", load_card)
    settings = SimpleNamespace(support_group_id=SUPPORT_CHAT_ID)

    await relay_user_message(
        first,
        session_factory=object(),  # type: ignore[arg-type]
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    await relay_user_message(
        repeated,
        session_factory=object(),  # type: ignore[arg-type]
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )

    bot.create_forum_topic.assert_awaited_once_with(
        chat_id=SUPPORT_CHAT_ID,
        name=build_topic_name(user),
        icon_color=SUPPORT_TOPIC_ICON_COLOR,
    )
    load_card.assert_awaited_once()
    card_calls = [
        item
        for item in bot.send_message.await_args_list
        if item.kwargs.get("text") == "<b>USER CARD</b>"
    ]
    assert len(card_calls) == 1
    assert all(
        item.kwargs["message_thread_id"] == TOPIC_ID
        for item in bot.send_message.await_args_list
    )
    first.copy_to.assert_awaited_once_with(
        chat_id=SUPPORT_CHAT_ID,
        message_thread_id=TOPIC_ID,
    )
    repeated.copy_to.assert_awaited_once_with(
        chat_id=SUPPORT_CHAT_ID,
        message_thread_id=TOPIC_ID,
    )
    first.answer.assert_awaited_once_with(
        EXPECTED_CONFIRMATION,
        entities=_confirmation_entities(),
    )
    repeated.answer.assert_not_awaited()
    assert await store.get_topic(USER_ID) == TOPIC_ID
    assert await store.get_user_for_topic(TOPIC_ID) == USER_ID


@pytest.mark.asyncio
async def test_concurrent_first_messages_create_exactly_one_topic(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    store = SupportStore(redis, SUPPORT_CHAT_ID)  # type: ignore[arg-type]
    await store.begin(USER_ID)
    bot = fake_bot()
    user = telegram_user()
    first = private_message(bot, user, text="Первое сообщение")
    second = private_message(bot, user, text="Второе сообщение")
    monkeypatch.setattr(
        support_service,
        "load_user_card",
        AsyncMock(return_value="<b>USER CARD</b>"),
    )
    kwargs = {
        "session_factory": object(),
        "redis_client": redis,
        "settings": SimpleNamespace(support_group_id=SUPPORT_CHAT_ID),
    }

    await asyncio.gather(
        relay_user_message(first, **kwargs),  # type: ignore[arg-type]
        relay_user_message(second, **kwargs),  # type: ignore[arg-type]
    )

    bot.create_forum_topic.assert_awaited_once()
    assert first.copy_to.await_count == 1
    assert second.copy_to.await_count == 1
    assert first.answer.await_count + second.answer.await_count == 1
    assert await store.get_topic(USER_ID) == TOPIC_ID


@pytest.mark.asyncio
async def test_failed_delivery_sends_failure_and_never_false_confirmation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    redis = FakeRedis()
    await SupportStore(  # type: ignore[arg-type]
        redis,
        SUPPORT_CHAT_ID,
    ).begin(USER_ID)
    bot = fake_bot()
    message = private_message(bot, telegram_user())
    message.copy_to.side_effect = RuntimeError("Telegram unavailable")
    monkeypatch.setattr(
        support_service,
        "load_user_card",
        AsyncMock(return_value="<b>USER CARD</b>"),
    )

    await relay_user_message(
        message,
        session_factory=object(),  # type: ignore[arg-type]
        redis_client=redis,  # type: ignore[arg-type]
        settings=SimpleNamespace(  # type: ignore[arg-type]
            support_group_id=SUPPORT_CHAT_ID
        ),
    )

    message.answer.assert_awaited_once_with(SUPPORT_DELIVERY_FAILED)
    assert all(
        call.args != (SUPPORT_CONFIRMATION,)
        for call in message.answer.await_args_list
    )
    bot.delete_message.assert_awaited_once_with(
        chat_id=SUPPORT_CHAT_ID,
        message_id=900,
    )


@pytest.mark.asyncio
async def test_admin_topic_reply_is_resolved_and_copied_to_user() -> None:
    redis = FakeRedis()
    store = SupportStore(redis, SUPPORT_CHAT_ID)  # type: ignore[arg-type]
    await store.bind_topic(USER_ID, TOPIC_ID)
    admin = telegram_user(user_id=ADMIN_ID, first_name="Администратор")
    message = SimpleNamespace(
        bot=fake_bot(),
        chat=SimpleNamespace(id=SUPPORT_CHAT_ID, type=ChatType.SUPERGROUP),
        message_thread_id=TOPIC_ID,
        content_type=ContentType.DOCUMENT,
        entities=None,
        caption_entities=None,
        text=None,
        caption="Ответ с документом",
        from_user=admin,
        sender_chat=None,
        copy_to=AsyncMock(),
    )

    result = await SupportAdminMessageFilter()(
        message,
        redis_client=redis,  # type: ignore[arg-type]
        settings=SimpleNamespace(  # type: ignore[arg-type]
            support_group_id=SUPPORT_CHAT_ID
        ),
        admin_ids={ADMIN_ID},
    )

    assert result == {"support_user_id": USER_ID}
    await relay_admin_message(message, support_user_id=USER_ID)
    message.copy_to.assert_awaited_once_with(chat_id=USER_ID)


@pytest.mark.asyncio
async def test_support_chat_id_command_reports_forum_id_to_admin() -> None:
    admin = telegram_user(user_id=ADMIN_ID, first_name="Администратор")
    message = SimpleNamespace(
        bot=fake_bot(),
        chat=SimpleNamespace(
            id=SUPPORT_CHAT_ID,
            type=ChatType.SUPERGROUP,
            is_forum=True,
        ),
        from_user=admin,
        sender_chat=None,
        answer=AsyncMock(),
    )

    await show_support_chat_id(message, admin_ids={ADMIN_ID})  # type: ignore[arg-type]

    message.answer.assert_awaited_once_with(
        f"🆔 ID forum-группы: <code>{SUPPORT_CHAT_ID}</code>",
        parse_mode="HTML",
    )


@pytest.mark.asyncio
async def test_close_topic_clears_user_mode_and_both_mappings() -> None:
    redis = FakeRedis()
    store = SupportStore(redis, SUPPORT_CHAT_ID)  # type: ignore[arg-type]
    await store.begin(USER_ID)
    await store.bind_topic(USER_ID, TOPIC_ID)
    admin = telegram_user(user_id=ADMIN_ID, first_name="Администратор")
    topic_message = Message(
        message_id=50,
        date=datetime.now(UTC),
        chat=Chat(
            id=SUPPORT_CHAT_ID,
            type=ChatType.SUPERGROUP,
            title="Support",
            is_forum=True,
        ),
        from_user=admin,
        message_thread_id=TOPIC_ID,
        text="Карточка",
    )
    bot = fake_bot()
    callback = SimpleNamespace(
        message=topic_message,
        from_user=admin,
        bot=bot,
        answer=AsyncMock(),
    )

    await close_support_case(
        callback,  # type: ignore[arg-type]
        redis_client=redis,  # type: ignore[arg-type]
        settings=SimpleNamespace(  # type: ignore[arg-type]
            support_group_id=SUPPORT_CHAT_ID
        ),
        admin_ids={ADMIN_ID},
    )

    bot.close_forum_topic.assert_awaited_once_with(
        chat_id=SUPPORT_CHAT_ID,
        message_thread_id=TOPIC_ID,
    )
    assert await store.get_mode(USER_ID) is None
    assert await store.get_topic(USER_ID) is None
    assert await store.get_user_for_topic(TOPIC_ID) is None
    callback.answer.assert_awaited_once()


@pytest.mark.asyncio
async def test_commands_never_pass_active_support_filter() -> None:
    redis = FakeRedis()
    store = SupportStore(redis, SUPPORT_CHAT_ID)  # type: ignore[arg-type]
    await store.begin(USER_ID)
    settings = SimpleNamespace(support_group_id=SUPPORT_CHAT_ID)
    user = telegram_user()
    bot = fake_bot()
    regular = private_message(bot, user, text="Ещё одно сообщение")
    slash_command = private_message(bot, user, text="  /start payload")
    entity_command = private_message(bot, user, text="/key")
    entity_command.entities = [
        MessageEntity(
            type=MessageEntityType.BOT_COMMAND,
            offset=0,
            length=4,
        )
    ]
    support_filter = SupportModeFilter()

    assert await support_filter(
        regular,
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    assert not await support_filter(
        slash_command,
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    assert not await support_filter(
        entity_command,
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_support_filter_restores_legacy_waiting_mode() -> None:
    redis = FakeRedis()
    await SupportStore(redis, None).begin(USER_ID)  # type: ignore[arg-type]
    settings = SimpleNamespace(support_group_id=SUPPORT_CHAT_ID)
    message = private_message(
        fake_bot(),
        telegram_user(),
        text="Сообщение после настройки группы",
    )

    assert await SupportModeFilter()(
        message,
        redis_client=redis,  # type: ignore[arg-type]
        settings=settings,  # type: ignore[arg-type]
    )
    assert (
        await SupportStore(  # type: ignore[arg-type]
            redis,
            SUPPORT_CHAT_ID,
        ).get_mode(USER_ID)
        == "waiting"
    )
