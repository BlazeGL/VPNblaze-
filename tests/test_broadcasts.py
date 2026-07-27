import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ContentType
from aiogram.exceptions import TelegramForbiddenError, TelegramRetryAfter
from aiogram.methods import CopyMessage

from app.bot.callbacks import AdminCallback
from app.bot.handlers import admin
from app.bot.handlers.admin import BroadcastForm, capture_broadcast_message
from app.services.broadcasts import BroadcastResult, copy_broadcast_to_users


def async_context(value: object) -> MagicMock:
    context = MagicMock()
    context.__aenter__ = AsyncMock(return_value=value)
    context.__aexit__ = AsyncMock(return_value=False)
    return context


def readonly_factory(session: MagicMock) -> MagicMock:
    return MagicMock(return_value=async_context(session))


def copy_method(chat_id: int) -> CopyMessage:
    return CopyMessage(
        chat_id=chat_id,
        from_chat_id=700,
        message_id=55,
    )


@pytest.mark.asyncio
async def test_broadcast_continues_after_forbidden_and_unexpected_errors() -> None:
    bot = MagicMock()
    bot.copy_message = AsyncMock(
        side_effect=[
            TelegramForbiddenError(
                method=copy_method(101),
                message="bot was blocked by the user",
            ),
            object(),
            RuntimeError("temporary local failure"),
            object(),
        ]
    )

    result = await copy_broadcast_to_users(
        bot,
        [101, 202, 303, 404],
        from_chat_id=700,
        message_id=55,
        delay_seconds=0,
    )

    assert result == BroadcastResult(successful=2, errors=2)
    assert result.total == 4
    assert [call.kwargs["chat_id"] for call in bot.copy_message.await_args_list] == [
        101,
        202,
        303,
        404,
    ]
    for call in bot.copy_message.await_args_list:
        assert call.kwargs["from_chat_id"] == 700
        assert call.kwargs["message_id"] == 55


@pytest.mark.asyncio
async def test_broadcast_retries_same_user_after_retry_after() -> None:
    bot = MagicMock()
    bot.copy_message = AsyncMock(
        side_effect=[
            TelegramRetryAfter(
                method=copy_method(101),
                message="too many requests",
                retry_after=2,
            ),
            object(),
        ]
    )
    sleep = AsyncMock()

    result = await copy_broadcast_to_users(
        bot,
        [101],
        from_chat_id=700,
        message_id=55,
        delay_seconds=0,
        sleep=sleep,
    )

    assert result == BroadcastResult(successful=1, errors=0)
    assert [call.kwargs["chat_id"] for call in bot.copy_message.await_args_list] == [
        101,
        101,
    ]
    sleep.assert_awaited_once()
    assert sleep.await_args.args[0] == pytest.approx(2.1)


@pytest.mark.asyncio
async def test_capture_broadcast_message_stores_source_in_fsm_read_only() -> None:
    session = MagicMock()
    session.scalar = AsyncMock(return_value=3)
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    state = MagicMock()
    state.update_data = AsyncMock()
    state.set_state = AsyncMock()
    message = MagicMock()
    message.content_type = ContentType.TEXT
    message.media_group_id = None
    message.chat = SimpleNamespace(id=700)
    message.message_id = 55
    message.answer = AsyncMock()

    await capture_broadcast_message(
        message,
        state,
        readonly_factory(session),
    )

    session.scalar.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()
    state.update_data.assert_awaited_once_with(
        source_chat_id=700,
        source_message_id=55,
    )
    state.set_state.assert_awaited_once_with(BroadcastForm.confirm)
    message.answer.assert_awaited_once()
    assert "Получателей: 3" in message.answer.await_args.args[0]


@pytest.mark.asyncio
async def test_broadcast_handler_sends_to_all_ids_and_reports_statistics(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=[101, 202, 303])
    session.flush = AsyncMock()
    session.commit = AsyncMock()
    factory = readonly_factory(session)

    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={
            "source_chat_id": 700,
            "source_message_id": 55,
        }
    )
    state.get_state = AsyncMock(return_value=BroadcastForm.confirm.state)
    state.set_state = AsyncMock()
    state.clear = AsyncMock()

    callback = MagicMock()
    callback.bot = MagicMock()
    callback.from_user = SimpleNamespace(id=42)
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    delivery = AsyncMock(return_value=BroadcastResult(successful=2, errors=1))
    monkeypatch.setattr(admin, "copy_broadcast_to_users", delivery)

    await admin.admin_actions(
        callback,
        AdminCallback(action="broadcast_send"),
        factory,
        state,
    )

    delivery.assert_awaited_once_with(
        callback.bot,
        [101, 202, 303],
        from_chat_id=700,
        message_id=55,
    )
    callback.answer.assert_awaited_once_with("Рассылка запущена")
    state.set_state.assert_awaited_once_with(BroadcastForm.sending)
    state.clear.assert_awaited_once_with()
    assert callback.message.edit_text.await_count == 2
    final_message = callback.message.edit_text.await_args_list[-1]
    assert "Успешно отправлено: 2" in final_message.args[0]
    assert "Ошибок: 1" in final_message.args[0]

    session.scalars.assert_awaited_once()
    session.add.assert_not_called()
    session.flush.assert_not_awaited()
    session.commit.assert_not_awaited()


@pytest.mark.asyncio
async def test_second_confirmation_cannot_start_duplicate_broadcast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    session = MagicMock()
    session.scalars = AsyncMock(return_value=[101])
    factory = readonly_factory(session)
    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={"source_chat_id": 700, "source_message_id": 55}
    )
    state.get_state = AsyncMock(return_value=BroadcastForm.confirm.state)
    state.set_state = AsyncMock()
    state.clear = AsyncMock()

    started = asyncio.Event()
    release = asyncio.Event()

    async def slow_delivery(*args: object, **kwargs: object) -> BroadcastResult:
        started.set()
        await release.wait()
        return BroadcastResult(successful=1, errors=0)

    delivery = AsyncMock(side_effect=slow_delivery)
    monkeypatch.setattr(admin, "copy_broadcast_to_users", delivery)

    first = MagicMock()
    first.bot = MagicMock()
    first.from_user = SimpleNamespace(id=42)
    first.message = MagicMock()
    first.message.edit_text = AsyncMock()
    first.answer = AsyncMock()
    second = MagicMock()
    second.bot = first.bot
    second.from_user = SimpleNamespace(id=42)
    second.message = first.message
    second.answer = AsyncMock()

    first_task = asyncio.create_task(
        admin.admin_actions(
            first,
            AdminCallback(action="broadcast_send"),
            factory,
            state,
        )
    )
    await asyncio.wait_for(started.wait(), timeout=1)
    await admin.admin_actions(
        second,
        AdminCallback(action="broadcast_send"),
        factory,
        state,
    )
    release.set()
    await first_task

    delivery.assert_awaited_once()
    second.answer.assert_awaited_once_with(
        "Эта рассылка уже выполняется.",
        show_alert=True,
    )
    assert 42 not in admin.ACTIVE_BROADCAST_ADMINS


@pytest.mark.asyncio
async def test_system_broadcast_failure_clears_state_and_returns_to_menu() -> None:
    session = MagicMock()
    session.scalars = AsyncMock(side_effect=RuntimeError("database unavailable"))
    state = MagicMock()
    state.get_data = AsyncMock(
        return_value={"source_chat_id": 700, "source_message_id": 55}
    )
    state.get_state = AsyncMock(return_value=BroadcastForm.confirm.state)
    state.set_state = AsyncMock()
    state.clear = AsyncMock()
    callback = MagicMock()
    callback.bot = MagicMock()
    callback.from_user = SimpleNamespace(id=42)
    callback.message = MagicMock()
    callback.message.edit_text = AsyncMock()
    callback.answer = AsyncMock()

    await admin.admin_actions(
        callback,
        AdminCallback(action="broadcast_send"),
        readonly_factory(session),
        state,
    )

    state.clear.assert_awaited_once_with()
    assert callback.message.edit_text.await_count == 2
    assert (
        "Рассылка не завершена"
        in (callback.message.edit_text.await_args_list[-1].args[0])
    )
    assert 42 not in admin.ACTIVE_BROADCAST_ADMINS
