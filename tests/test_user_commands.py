from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatType
from aiogram.types import BotCommandScopeAllPrivateChats, BotCommandScopeChat

from app.bot.handlers import setup_routers, user_commands
from app.bot.handlers.promos import PromoInput, enter_promo_from_menu
from app.bot.handlers.user_commands import (
    PRIVATE_COMMANDS,
    _transaction_line,
    key_command,
    profile_command,
)
from app.bot.services.command_menu import (
    ADMIN_COMMANDS,
    PUBLIC_COMMANDS,
    register_command_menu,
)
from app.database.models import BalanceTransactionType


def private_message(user_id: int = 123) -> MagicMock:
    message = MagicMock()
    message.chat.type = ChatType.PRIVATE
    message.from_user.id = user_id
    message.answer = AsyncMock()
    return message


@pytest.mark.asyncio
async def test_key_command_does_not_open_personal_data_in_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = private_message()
    message.chat.type = ChatType.GROUP
    message.bot.get_me = AsyncMock(
        return_value=SimpleNamespace(username="BlazeVPNBot")
    )
    render_key = AsyncMock()
    monkeypatch.setattr(user_commands, "render_key", render_key)

    await key_command(message, MagicMock())

    render_key.assert_not_awaited()
    answer = message.answer.await_args
    assert "личных сообщениях" in answer.args[0]
    button = answer.kwargs["reply_markup"].inline_keyboard[0][0]
    assert button.url == "https://t.me/BlazeVPNBot"


@pytest.mark.asyncio
async def test_profile_command_reuses_existing_subscription_renderer(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    message = private_message()
    renderer = AsyncMock()
    monkeypatch.setattr(user_commands, "show_subscription", renderer)
    session_factory = MagicMock()
    remnawave = MagicMock()
    cipher = MagicMock()

    await profile_command(message, session_factory, remnawave, cipher)

    renderer.assert_awaited_once()
    args = renderer.await_args.args
    assert args[1:] == (session_factory, remnawave, cipher)
    assert args[0].from_user.id == message.from_user.id


@pytest.mark.asyncio
async def test_promo_entry_clears_conflicting_state_and_starts_existing_fsm(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = MagicMock()
    callback.from_user.id = 123
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    state = MagicMock()
    state.clear = AsyncMock()
    state.set_state = AsyncMock()
    state.update_data = AsyncMock()
    order = SimpleNamespace(id="5f742b69-702f-48d5-8184-ddcf01fb7e28")

    user_repository = MagicMock()
    user_repository.get_by_telegram_id = AsyncMock(
        return_value=SimpleNamespace(id=7)
    )
    order_repository = MagicMock()
    order_repository.get_latest_pending_for_user = AsyncMock(return_value=order)
    monkeypatch.setattr(
        "app.bot.handlers.promos.UserRepository",
        MagicMock(return_value=user_repository),
    )
    monkeypatch.setattr(
        "app.bot.handlers.promos.OrderRepository",
        MagicMock(return_value=order_repository),
    )
    session_factory = MagicMock()

    await enter_promo_from_menu(callback, state, session_factory)

    state.clear.assert_awaited_once()
    state.set_state.assert_awaited_once_with(PromoInput.code)
    state.update_data.assert_awaited_once_with(order_id=str(order.id))
    prompt = callback.message.answer.await_args
    assert "Активация промокода" in prompt.args[0]


def test_balance_operation_is_rendered_for_user() -> None:
    transaction = SimpleNamespace(
        amount=Decimal("-199.00"),
        type=BalanceTransactionType.subscription_purchase,
    )

    assert _transaction_line(transaction) == "• −199 ₽ — Покупка подписки"  # type: ignore[arg-type]


def test_private_command_set_matches_security_requirement() -> None:
    assert PRIVATE_COMMANDS == {
        "key",
        "profile",
        "balance",
        "topup",
        "promo",
        "ref",
    }


@pytest.mark.asyncio
async def test_command_menu_has_public_and_admin_scopes() -> None:
    bot = MagicMock()
    bot.set_my_commands = AsyncMock()

    await register_command_menu(bot, {202, 101})

    calls = bot.set_my_commands.await_args_list
    assert isinstance(calls[0].kwargs["scope"], BotCommandScopeAllPrivateChats)
    assert [call.kwargs["scope"].chat_id for call in calls[1:]] == [101, 202]
    assert all(
        isinstance(call.kwargs["scope"], BotCommandScopeChat)
        for call in calls[1:]
    )
    public_names = {command.command for command in PUBLIC_COMMANDS}
    admin_names = {command.command for command in ADMIN_COMMANDS}
    assert admin_names.isdisjoint(public_names)
    assert admin_names == {
        "edik",
        "new_promo",
        "ref_stats",
        "sync_remnawave",
        "grant_vpn",
    }


def test_all_command_routers_are_registered() -> None:
    root = setup_routers()
    names: set[str] = set()

    def collect(router: object) -> None:
        for subrouter in router.sub_routers:  # type: ignore[attr-defined]
            names.add(subrouter.name)
            collect(subrouter)

    collect(root)

    assert "app.bot.handlers.user_commands" in names
    assert "app.bot.handlers.user_commands.unknown" in names
    assert "app.bot.handlers.admin_promos" in names
