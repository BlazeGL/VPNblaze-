import struct
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock
from xml.etree import ElementTree

import pytest
from aiogram.enums import ParseMode
from aiogram.types import FSInputFile

from app.bot.handlers.start import START_BANNER_PATH, send_welcome, show_channel
from app.bot.keyboards.start import (
    ACTIVATE_TRIAL_CALLBACK,
    BUY_SUBSCRIPTION_CALLBACK,
    CHANNEL_CALLBACK,
    CHANNEL_URL,
    MAIN_MENU_CALLBACK,
    MY_SUBSCRIPTION_CALLBACK,
    START_CONNECTION_CALLBACK,
    TARIFFS_CALLBACK,
    USER_AGREEMENT_CALLBACK,
    build_connection_menu,
    build_main_menu,
    channel_menu,
)
from app.bot.rendering import edit_text_or_caption
from app.bot.texts.start import CHANNEL_TEXT, START_TEXT


def flatten(markup: object) -> list[object]:
    return [button for row in markup.inline_keyboard for button in row]  # type: ignore[attr-defined]


def test_start_text_is_valid_html_caption_without_embedded_terms_link() -> None:
    ElementTree.fromstring(f"<root>{START_TEXT}</root>")

    assert len(START_TEXT) <= 1024
    assert "href=" not in START_TEXT
    assert "<b>Добро пожаловать в BlazeVPN!</b>" in START_TEXT
    assert "<b>30-дневный бесплатный период</b>" in START_TEXT


def test_start_keyboard_has_requested_layout_and_callbacks() -> None:
    markup = build_main_menu()

    assert [[button.text for button in row] for row in markup.inline_keyboard] == [
        ["🚀 Начать подключение"],
        ["💳 Купить подписку", "📦 Тарифы"],
        ["❓ Помощь", "👤 Личный кабинет"],
        ["📢 Наш канал"],
        ["📄 Пользовательское соглашение"],
    ]
    buttons = flatten(markup)
    assert buttons[0].callback_data == START_CONNECTION_CALLBACK  # type: ignore[attr-defined]
    assert buttons[1].callback_data == BUY_SUBSCRIPTION_CALLBACK  # type: ignore[attr-defined]
    assert buttons[2].callback_data == TARIFFS_CALLBACK  # type: ignore[attr-defined]
    assert buttons[3].callback_data == "support_from_main"  # type: ignore[attr-defined]
    assert buttons[3].url is None  # type: ignore[attr-defined]
    assert buttons[4].callback_data == MY_SUBSCRIPTION_CALLBACK  # type: ignore[attr-defined]
    assert buttons[5].callback_data == CHANNEL_CALLBACK  # type: ignore[attr-defined]
    assert buttons[6].callback_data == USER_AGREEMENT_CALLBACK  # type: ignore[attr-defined]


def test_channel_url_is_hidden_behind_the_requested_button() -> None:
    markup = channel_menu()
    channel_button = markup.inline_keyboard[0][0]
    back_button = markup.inline_keyboard[1][0]

    assert channel_button.text == "BlazeVPN - News"
    assert channel_button.url == CHANNEL_URL
    assert channel_button.callback_data is None
    assert CHANNEL_URL not in CHANNEL_TEXT
    assert back_button.callback_data == MAIN_MENU_CALLBACK


@pytest.mark.asyncio
async def test_channel_callback_renders_message_without_visible_url(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    callback = SimpleNamespace(
        answer=AsyncMock(),
        message=SimpleNamespace(),
    )
    renderer = AsyncMock()
    monkeypatch.setattr("app.bot.handlers.start.edit_text_or_caption", renderer)

    await show_channel(callback)  # type: ignore[arg-type]

    callback.answer.assert_awaited_once()
    renderer.assert_awaited_once()
    assert renderer.await_args.args[1] == CHANNEL_TEXT
    assert CHANNEL_URL not in renderer.await_args.args[1]
    assert renderer.await_args.args[2].inline_keyboard[0][0].url == CHANNEL_URL


def test_valid_agreement_url_creates_url_only_button() -> None:
    button = flatten(
        build_main_menu("https://legal.example.org/blazevpn")
    )[-1]

    assert button.url == "https://legal.example.org/blazevpn"  # type: ignore[attr-defined]
    assert button.callback_data is None  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    "url",
    [None, "", "http://legal.example.org", "legal.example.org", "https://bad url"],
)
def test_missing_or_invalid_agreement_url_uses_internal_page(
    url: str | None,
) -> None:
    button = flatten(build_main_menu(url))[-1]

    assert button.url is None  # type: ignore[attr-defined]
    assert button.callback_data == USER_AGREEMENT_CALLBACK  # type: ignore[attr-defined]


@pytest.mark.parametrize(
    ("trial_available", "has_subscription", "expected_callbacks"),
    [
        (
            True,
            False,
            [
                ACTIVATE_TRIAL_CALLBACK,
                BUY_SUBSCRIPTION_CALLBACK,
                MAIN_MENU_CALLBACK,
            ],
        ),
        (
            False,
            True,
            [
                MY_SUBSCRIPTION_CALLBACK,
                BUY_SUBSCRIPTION_CALLBACK,
                MAIN_MENU_CALLBACK,
            ],
        ),
        (
            True,
            True,
            [
                ACTIVATE_TRIAL_CALLBACK,
                MY_SUBSCRIPTION_CALLBACK,
                BUY_SUBSCRIPTION_CALLBACK,
                MAIN_MENU_CALLBACK,
            ],
        ),
    ],
)
def test_connection_menu_reflects_user_state(
    trial_available: bool,
    has_subscription: bool,
    expected_callbacks: list[str],
) -> None:
    markup = build_connection_menu(
        trial_available=trial_available,
        has_subscription=has_subscription,
    )

    assert [
        button.callback_data  # type: ignore[attr-defined]
        for button in flatten(markup)
    ] == expected_callbacks


def test_start_banner_is_valid_telegram_photo() -> None:
    data = Path(START_BANNER_PATH).read_bytes()
    width, height = struct.unpack(">II", data[16:24])

    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    assert width > height
    assert len(data) < 10 * 1024 * 1024


@pytest.mark.asyncio
async def test_start_sends_photo_with_html_caption() -> None:
    message = SimpleNamespace(photo=None, answer_photo=AsyncMock())

    await send_welcome(message)  # type: ignore[arg-type]

    message.answer_photo.assert_awaited_once()
    call = message.answer_photo.await_args
    assert isinstance(call.kwargs["photo"], FSInputFile)
    assert call.kwargs["caption"] == START_TEXT
    assert call.kwargs["parse_mode"] == ParseMode.HTML


@pytest.mark.asyncio
async def test_photo_navigation_edits_caption_in_place() -> None:
    message = SimpleNamespace(
        photo=[object()],
        edit_caption=AsyncMock(),
        edit_text=AsyncMock(),
        answer=AsyncMock(),
    )

    await edit_text_or_caption(  # type: ignore[arg-type]
        message,
        "Новое меню",
        build_main_menu(),
        parse_mode=ParseMode.HTML,
    )

    message.edit_caption.assert_awaited_once()
    message.edit_text.assert_not_awaited()
    message.answer.assert_not_awaited()
