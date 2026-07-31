from datetime import UTC, datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from aiogram.enums import ChatType

from app.bot.handlers import trial
from app.bot.handlers.apps import _owned_subscription, show_short_link
from app.bot.handlers.trial import show_subscription
from app.bot.keyboards.main_menu import build_main_menu
from app.bot.keyboards.subscription import (
    activation_keyboard,
    back_keyboard,
    devices_keyboard,
    instruction_keyboard,
    platform_keyboard,
    subscription_menu,
)
from app.bot.texts.account import (
    account_text,
    format_time_left,
    format_traffic,
    get_subscription_status_text,
)
from app.bot.texts.subscription import INSTRUCTION_TEXT, PLATFORM_TEXTS, activation_text
from app.database.models import (
    ProvisioningStatus,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)

APP_URLS = {
    "android": "https://play.google.com/store/apps/details?id=llc.itdev.incy",
    "ios": "https://apps.apple.com/ru/app/incy/id6756943388",
    "windows": (
        "https://github.com/hiddify/hiddify-next/releases/latest/download/"
        "Hiddify-Windows-Setup-x64.exe"
    ),
    "linux": (
        "https://github.com/hiddify/hiddify-app/releases/latest/download/"
        "Hiddify-Linux-x64.AppImage"
    ),
}


def flatten(markup: object) -> list[object]:
    return [button for row in markup.inline_keyboard for button in row]  # type: ignore[attr-defined]


def test_key_screen_contains_individual_url_and_escapes_html() -> None:
    subscription = SimpleNamespace(expires_at=datetime(2026, 8, 1, tzinfo=UTC))
    url = "https://panel.example/sub/me?token=a&device=<phone>"

    text = activation_text(subscription, url)  # type: ignore[arg-type]

    assert "Ваш BlazeVPN активирован" in text
    assert "https://panel.example/sub/me?token=a&amp;device=&lt;phone&gt;" in text
    assert "01.08.2026" in text


@pytest.mark.parametrize(("platform", "url"), APP_URLS.items())
def test_download_buttons_use_configured_urls(platform: str, url: str) -> None:
    buttons = flatten(platform_keyboard(platform, url))

    assert buttons[0].url == url  # type: ignore[attr-defined]
    assert buttons[0].callback_data is None  # type: ignore[attr-defined]


def test_missing_or_unsafe_download_url_is_hidden() -> None:
    assert all(
        button.url is None for button in flatten(platform_keyboard("android", None))
    )  # type: ignore[attr-defined]
    assert all(
        button.url is None
        for button in flatten(platform_keyboard("android", "http://unsafe.test/app"))  # type: ignore[attr-defined]
    )


def test_back_navigation_from_android_and_devices_is_explicit() -> None:
    android_buttons = flatten(platform_keyboard("android", APP_URLS["android"]))
    device_buttons = flatten(devices_keyboard())

    assert android_buttons[-1].callback_data == "back_to_devices"  # type: ignore[attr-defined]
    assert device_buttons[-1].callback_data == "back_to_key"  # type: ignore[attr-defined]


def test_urls_and_subscription_values_are_not_stored_in_callback_data() -> None:
    callbacks = [
        button.callback_data
        for markup in (
            activation_keyboard(),
            devices_keyboard(),
            *(platform_keyboard(platform, url) for platform, url in APP_URLS.items()),
        )
        for button in flatten(markup)
        if button.callback_data
    ]

    assert all("https://" not in value for value in callbacks)
    assert all("token" not in value for value in callbacks)


def test_unknown_navigation_destination_is_rejected() -> None:
    with pytest.raises(ValueError, match="Unsupported navigation destination"):
        back_keyboard("user_controlled_destination")


def test_instruction_has_clear_steps_and_direct_action_buttons() -> None:
    assert "Установите приложение" in INSTRUCTION_TEXT
    assert "Скопируйте персональную ссылку" in INSTRUCTION_TEXT
    assert "Добавьте ссылку в приложение" in INSTRUCTION_TEXT
    assert "Включите VPN" in INSTRUCTION_TEXT
    assert "Из буфера обмена" in INSTRUCTION_TEXT

    buttons = flatten(instruction_keyboard())
    assert [button.callback_data for button in buttons] == [  # type: ignore[attr-defined]
        "apps",
        "key_refresh",
        "support_from_key",
        "back_to_key",
    ]


@pytest.mark.parametrize("platform", ["android", "ios", "windows", "linux"])
def test_platform_instructions_end_with_connection_confirmation(
    platform: str,
) -> None:
    assert "Показать мой ключ" in PLATFORM_TEXTS[platform]
    assert "из буфера обмена" in PLATFORM_TEXTS[platform]
    assert "Подключено" in PLATFORM_TEXTS[platform]


def test_support_buttons_use_internal_callbacks() -> None:
    main_support = next(
        button
        for button in flatten(build_main_menu())
        if button.text == "❓ Помощь"  # type: ignore[attr-defined]
    )
    key_support = flatten(activation_keyboard())[-2]
    subscription_support = next(
        button
        for button in flatten(subscription_menu())
        if button.text == "🆘 Поддержка"  # type: ignore[attr-defined]
    )

    assert main_support.callback_data == "support_from_main"  # type: ignore[attr-defined]
    assert key_support.callback_data == "support_from_key"  # type: ignore[attr-defined]
    assert (  # type: ignore[attr-defined]
        subscription_support.callback_data == "support_from_subscription"
    )
    assert main_support.url is None  # type: ignore[attr-defined]
    assert key_support.url is None  # type: ignore[attr-defined]
    assert subscription_support.url is None  # type: ignore[attr-defined]


@pytest.mark.asyncio
async def test_subscription_lookup_is_scoped_to_callback_owner() -> None:
    statements: list[object] = []
    current_user = User(id=17, telegram_id=777)
    session = MagicMock()

    async def scalar(statement: object) -> object | None:
        statements.append(statement)
        return current_user if len(statements) == 1 else None

    session.scalar = scalar
    user, subscription = await _owned_subscription(session, 777)

    assert user is current_user
    assert subscription is None
    assert list(statements[0].compile().params.values()) == [777]  # type: ignore[union-attr]
    assert list(statements[1].compile().params.values()) == [17]  # type: ignore[union-attr]


@pytest.mark.asyncio
async def test_subscription_link_is_never_sent_to_group_chat() -> None:
    callback = MagicMock()
    callback.message.chat.type = ChatType.GROUP
    callback.answer = AsyncMock()
    callback.message.answer = AsyncMock()
    session_factory = MagicMock()

    await show_short_link(callback, session_factory)

    session_factory.assert_not_called()
    callback.message.answer.assert_not_awaited()
    callback.answer.assert_awaited_once_with(
        "Для безопасности откройте бота в личных сообщениях.", show_alert=True
    )


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        ("active", "🟢 Активна"),
        ("pending", "🟡 Готовится"),
        ("expired", "🔴 Закончилась"),
        ("activation_failed", "🟠 Требуется помощь"),
    ],
)
def test_subscription_statuses_are_human_readable(
    status: str,
    expected: str,
) -> None:
    assert get_subscription_status_text(status) == expected


def test_negative_remaining_time_is_not_displayed() -> None:
    now = datetime(2026, 8, 2, tzinfo=UTC)
    expired = datetime(2026, 8, 1, tzinfo=UTC)

    assert format_time_left(expired, now=now) == "срок закончился"


def test_remaining_time_uses_russian_plural_forms() -> None:
    now = datetime(2026, 8, 1, tzinfo=UTC)

    assert format_time_left(
        datetime(2026, 8, 2, tzinfo=UTC), now=now
    ) == "1 день"
    assert format_time_left(
        datetime(2026, 8, 3, tzinfo=UTC), now=now
    ) == "2 дня"
    assert format_time_left(
        datetime(2026, 8, 6, tzinfo=UTC), now=now
    ) == "5 дней"


def make_subscription(**overrides: object) -> SimpleNamespace:
    values: dict[str, object] = {
        "status": SubscriptionStatus.active,
        "source_type": SubscriptionSource.paid,
        "provisioning_status": ProvisioningStatus.active,
        "expires_at": datetime(2026, 9, 1, tzinfo=UTC),
        "traffic_limit_gb": 200,
        "is_unlimited_traffic": False,
        "used_traffic_bytes": int(34.2 * 1024**3),
        "device_limit": 3,
    }
    values.update(overrides)
    return SimpleNamespace(**values)


def test_traffic_is_rendered_in_gigabytes() -> None:
    subscription = make_subscription()

    assert format_traffic(subscription) == "34,2 ГБ из 200 ГБ"  # type: ignore[arg-type]


def test_unlimited_traffic_has_plain_language_label() -> None:
    subscription = make_subscription(is_unlimited_traffic=True)

    assert format_traffic(subscription) == "Без ограничений"  # type: ignore[arg-type]


def test_account_hides_technical_fields_and_keeps_local_data_on_sync_error() -> None:
    subscription = make_subscription()

    text, state = account_text(  # type: ignore[arg-type]
        subscription,
        "На 3 месяца",
        now=datetime(2026, 8, 1, tzinfo=UTC),
        sync_unavailable=True,
    )

    assert state == "active"
    assert "На 3 месяца" in text
    assert "последняя сохранённая информация" in text
    assert "UUID" not in text
    assert "provisioning" not in text
    assert "Remnawave" not in text


def test_user_without_subscription_sees_purchase_without_repeated_trial() -> None:
    callbacks = [
        button.callback_data
        for button in flatten(
            subscription_menu(
                state="none",
                trial_available=False,
                has_key=False,
            )
        )
    ]

    assert "buy_vpn" in callbacks
    assert "tariffs" in callbacks
    assert "activate_trial" not in callbacks


def test_expired_subscription_sees_renewal_and_previous_key() -> None:
    buttons = flatten(subscription_menu(state="expired", has_key=True))

    assert [button.text for button in buttons[:2]] == [  # type: ignore[attr-defined]
        "💳 Возобновить подписку",
        "🔑 Показать прежний ключ",
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "callback_data",
    [
        "my_subscription",
        "my_subscription_from_key",
        "subscription_refresh",
        "back_to_subscription",
    ],
)
async def test_subscription_is_synced_every_time_account_is_opened(
    callback_data: str,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    events: list[str] = []
    callback = MagicMock()
    callback.data = callback_data
    callback.from_user.id = 777
    callback.message.chat.type = ChatType.PRIVATE
    callback.answer = AsyncMock(side_effect=lambda: events.append("answered"))

    user = User(id=17, telegram_id=777)
    user.balance = 0
    user.total_referrals = 0
    user.total_referral_income = 0
    subscription = make_subscription(
        user_id=user.id,
        tariff_id=None,
        remnawave_sync_error=None,
        subscription_url_encrypted=b"encrypted",
    )
    session = MagicMock()
    session.scalar = AsyncMock(side_effect=[user, subscription])
    transaction = MagicMock()
    transaction.__aenter__ = AsyncMock(return_value=None)
    transaction.__aexit__ = AsyncMock(return_value=False)
    session.begin.return_value = transaction
    session_context = MagicMock()
    session_context.__aenter__ = AsyncMock(return_value=session)
    session_context.__aexit__ = AsyncMock(return_value=False)
    session_factory = MagicMock(return_value=session_context)

    sync_one = AsyncMock(side_effect=lambda *_: events.append("synced"))
    sync_service = MagicMock()
    sync_service.sync_one = sync_one
    sync_service_factory = MagicMock(return_value=sync_service)
    monkeypatch.setattr(trial, "RemnawaveSyncService", sync_service_factory)
    renderer = AsyncMock()
    monkeypatch.setattr(trial, "edit_text_or_caption", renderer)

    remnawave_client = MagicMock()
    subscription_cipher = MagicMock()
    await show_subscription(
        callback,
        session_factory,
        remnawave_client,
        subscription_cipher,
    )

    assert events == ["answered", "synced"]
    sync_service_factory.assert_called_once_with(
        session,
        remnawave_client,
        subscription_cipher,
    )
    sync_one.assert_awaited_once_with(subscription, user)
    renderer.assert_awaited_once()
