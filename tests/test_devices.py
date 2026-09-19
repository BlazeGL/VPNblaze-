from uuid import UUID

from app.bot.handlers.devices import (
    CONFIRM_PREFIX,
    RESET_PREFIX,
    _find_device,
    confirm_keyboard,
    device_name,
    device_token,
    devices_keyboard,
    devices_text,
)
from app.integrations.remnawave.schemas import HwidDevice, HwidDevicesData

USER_UUID = "bbbbbbbb-bbbb-4bbb-8bbb-bbbbbbbbbbbb"


def make_device(**overrides: object) -> HwidDevice:
    data: dict[str, object] = {
        "hwid": "A-device-identifier-123",
        "userUuid": UUID(USER_UUID),
        "platform": "iOS",
        "deviceModel": "iPhone 15 Pro",
    }
    data.update(overrides)
    return HwidDevice.model_validate(data)


def test_device_list_is_compact_and_uses_opaque_short_callbacks() -> None:
    device = make_device()
    keyboard = devices_keyboard(USER_UUID, [device])
    button = keyboard.inline_keyboard[0][0]

    assert button.text == "📱 iPhone 15 Pro · iOS"
    assert button.callback_data is not None
    assert button.callback_data.startswith(CONFIRM_PREFIX)
    assert device.hwid not in button.callback_data
    assert len(button.callback_data.encode()) <= 64
    assert keyboard.inline_keyboard[-1][0].callback_data == "back_to_subscription"


def test_device_token_can_be_resolved_only_against_current_user_list() -> None:
    device = make_device()
    devices = HwidDevicesData(total=1, devices=[device])
    token = device_token(USER_UUID, device.hwid)

    assert _find_device(USER_UUID, devices, token) == device
    assert _find_device("aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa", devices, token) is None


def test_identical_device_names_are_still_distinguishable() -> None:
    first = make_device(hwid="first-device-1111")
    second = make_device(hwid="second-device-2222")

    keyboard = devices_keyboard(USER_UUID, [first, second])

    assert keyboard.inline_keyboard[0][0].text.endswith("…1111")  # type: ignore[union-attr]
    assert keyboard.inline_keyboard[1][0].text.endswith("…2222")  # type: ignore[union-attr]


def test_device_copy_handles_empty_and_named_lists() -> None:
    assert devices_text(HwidDevicesData(total=0), 5) == (
        "📱 <b>Устройства</b>\n\nПодключённых устройств пока нет."
    )
    assert "1 из 5" in devices_text(
        HwidDevicesData(total=1, devices=[make_device()]), 5
    )
    assert device_name(make_device(deviceModel=None, platform="Android")) == "Android"


def test_confirmation_callback_stays_short() -> None:
    token = device_token(USER_UUID, "A-device-identifier-123")
    button = confirm_keyboard(token).inline_keyboard[0][0]

    assert button.callback_data == f"{RESET_PREFIX}{token}"
    assert len(button.callback_data.encode()) <= 64  # type: ignore[union-attr]
