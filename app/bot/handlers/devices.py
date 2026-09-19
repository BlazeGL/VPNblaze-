import hashlib
import logging
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.rendering import edit_text_or_caption
from app.bot.texts.account import get_account_state
from app.database.models import Subscription, User
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.remnawave.exceptions import RemnawaveError
from app.integrations.remnawave.schemas import HwidDevice, HwidDevicesData

router = Router(name=__name__)
logger = logging.getLogger(__name__)

MANAGE_DEVICES_CALLBACK = "manage_devices"
CONFIRM_PREFIX = "device_reset_confirm:"
RESET_PREFIX = "device_reset:"


def device_token(user_uuid: str, hwid: str) -> str:
    return hashlib.sha256(f"{user_uuid}:{hwid}".encode()).hexdigest()[:12]


def device_name(device: HwidDevice) -> str:
    model = (device.device_model or "").strip()
    platform = (device.platform or "").strip()
    if model and platform and platform.casefold() not in model.casefold():
        label = f"{model} · {platform}"
    else:
        label = model or platform or "Устройство"
    return label[:42]


def devices_keyboard(user_uuid: str, devices: list[HwidDevice]) -> InlineKeyboardMarkup:
    named_devices = [(device, device_name(device)) for device in devices if device.hwid]
    duplicate_names = {
        name.casefold()
        for _, name in named_devices
        if sum(other.casefold() == name.casefold() for _, other in named_devices) > 1
    }
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"📱 {name} · …{device.hwid[-4:]}"
                    if name.casefold() in duplicate_names
                    else f"📱 {name}"
                ),
                callback_data=f"{CONFIRM_PREFIX}{device_token(user_uuid, device.hwid)}",
            )
        ]
        for device, name in named_devices
    ]
    rows.append(
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="back_to_subscription")]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def confirm_keyboard(token: str) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сбросить",
                    callback_data=f"{RESET_PREFIX}{token}",
                ),
                InlineKeyboardButton(
                    text="Отмена",
                    callback_data=MANAGE_DEVICES_CALLBACK,
                ),
            ]
        ]
    )


def devices_text(data: HwidDevicesData, limit: int) -> str:
    if not any(device.hwid for device in data.devices):
        return "📱 <b>Устройства</b>\n\nПодключённых устройств пока нет."
    return (
        "📱 <b>Устройства</b>\n\n"
        f"Подключено: <b>{data.total} из {limit}</b>\n"
        "Выберите устройство, которое хотите сбросить:"
    )


async def _owned_subscription(
    session_factory: async_sessionmaker[AsyncSession], telegram_id: int
) -> Subscription | None:
    async with session_factory() as session, session.begin():
        user = await session.scalar(select(User).where(User.telegram_id == telegram_id))
        if user is None:
            return None
        return await session.scalar(
            select(Subscription).where(Subscription.user_id == user.id)
        )


async def _load_devices(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None,
) -> tuple[Subscription, HwidDevicesData] | None:
    subscription = await _owned_subscription(session_factory, callback.from_user.id)
    if (
        subscription is None
        or get_account_state(subscription) not in {"active", "trial"}
        or not subscription.remnawave_user_uuid
    ):
        await callback.answer("Активная подписка не найдена.", show_alert=True)
        return None
    if remnawave_client is None:
        await callback.answer("Устройства временно недоступны.", show_alert=True)
        return None
    # Remnawave may respond slowly, so acknowledge Telegram before the API call.
    await callback.answer()
    try:
        devices = await remnawave_client.get_user_hwid_devices(
            subscription.remnawave_user_uuid
        )
    except RemnawaveError:
        logger.exception(
            "Could not load devices for Telegram user %s", callback.from_user.id
        )
        await callback.answer(
            "Не удалось загрузить устройства. Попробуйте позже.", show_alert=True
        )
        return None
    return subscription, devices


def _find_device(
    user_uuid: str, devices: HwidDevicesData, token: str
) -> HwidDevice | None:
    return next(
        (
            device
            for device in devices.devices
            if device.hwid and device_token(user_uuid, device.hwid) == token
        ),
        None,
    )


def _private_message(callback: CallbackQuery) -> bool:
    return bool(
        callback.message is not None and callback.message.chat.type == ChatType.PRIVATE
    )


@router.callback_query(F.data == MANAGE_DEVICES_CALLBACK)
async def show_devices(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
) -> None:
    if not _private_message(callback):
        await callback.answer("Откройте бота в личных сообщениях.", show_alert=True)
        return
    loaded = await _load_devices(callback, session_factory, remnawave_client)
    if loaded is None or callback.message is None:
        return
    subscription, devices = loaded
    await edit_text_or_caption(
        callback.message,
        devices_text(devices, subscription.device_limit),
        devices_keyboard(subscription.remnawave_user_uuid or "", devices.devices),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith(CONFIRM_PREFIX))
async def confirm_device_reset(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
) -> None:
    if not _private_message(callback):
        await callback.answer("Откройте бота в личных сообщениях.", show_alert=True)
        return
    loaded = await _load_devices(callback, session_factory, remnawave_client)
    if loaded is None or callback.message is None:
        return
    subscription, devices = loaded
    token = (callback.data or "").removeprefix(CONFIRM_PREFIX)
    user_uuid = subscription.remnawave_user_uuid or ""
    device = _find_device(user_uuid, devices, token)
    if device is None:
        await callback.answer("Устройство уже сброшено.", show_alert=True)
        return
    await edit_text_or_caption(
        callback.message,
        "📱 <b>Сбросить устройство?</b>\n\n"
        f"{escape(device_name(device))}\n\n"
        "После сброса его потребуется подключить заново.",
        confirm_keyboard(token),
        parse_mode=ParseMode.HTML,
    )


@router.callback_query(F.data.startswith(RESET_PREFIX))
async def reset_device(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
) -> None:
    if not _private_message(callback):
        await callback.answer("Откройте бота в личных сообщениях.", show_alert=True)
        return
    loaded = await _load_devices(callback, session_factory, remnawave_client)
    if loaded is None or callback.message is None or remnawave_client is None:
        return
    subscription, devices = loaded
    token = (callback.data or "").removeprefix(RESET_PREFIX)
    user_uuid = subscription.remnawave_user_uuid or ""
    device = _find_device(user_uuid, devices, token)
    if device is None:
        await callback.answer("Устройство уже сброшено.", show_alert=True)
        return
    try:
        updated = await remnawave_client.delete_user_hwid_device(user_uuid, device.hwid)
    except RemnawaveError:
        logger.exception(
            "Could not reset a device for Telegram user %s", callback.from_user.id
        )
        await callback.answer(
            "Не удалось сбросить устройство. Попробуйте позже.", show_alert=True
        )
        return
    await edit_text_or_caption(
        callback.message,
        devices_text(updated, subscription.device_limit),
        devices_keyboard(user_uuid, updated.devices),
        parse_mode=ParseMode.HTML,
    )
