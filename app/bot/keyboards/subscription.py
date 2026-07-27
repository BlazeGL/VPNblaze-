import logging
from urllib.parse import urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

logger = logging.getLogger(__name__)
SUPPORT_URL = "https://t.me/Blaze_GL"

APP_LABELS = {
    "android": "📥 Скачать Incy",
    "ios": "📥 Скачать Incy",
    "windows": "📥 Скачать Hiddify для Windows",
    "linux": "📥 Скачать Hiddify для Linux",
}


def _safe_download_button(
    platform: str, url: str | None
) -> InlineKeyboardButton | None:
    if not url:
        logger.warning(
            "Download button for %s is hidden: URL is not configured", platform
        )
        return None
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.netloc:
        logger.warning("Download button for %s is hidden: invalid HTTPS URL", platform)
        return None
    return InlineKeyboardButton(text=APP_LABELS[platform], url=url)


def activation_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="📱 Скачать приложение", callback_data="apps")],
            [
                InlineKeyboardButton(
                    text="📖 Инструкция", callback_data="key_instruction"
                ),
                InlineKeyboardButton(
                    text="🔄 Показать ключ снова", callback_data="key_refresh"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="👤 Моя подписка", callback_data="my_subscription_from_key"
                ),
                InlineKeyboardButton(text="💳 Продлить", callback_data="tariffs"),
            ],
            [
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    callback_data="support_from_key",
                ),
                InlineKeyboardButton(text="🏠 Главное меню", callback_data="main_menu"),
            ],
        ]
    )


def devices_keyboard(*, source: str = "key") -> InlineKeyboardMarkup:
    sources = {
        "key": ("", "back_to_key"),
        "main": ("_main", "main_menu"),
        "subscription": ("_subscription", "back_to_subscription"),
    }
    if source not in sources:
        raise ValueError("Unsupported device navigation source")
    suffix, back_callback = sources[source]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🤖 Android", callback_data=f"app_android{suffix}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🍏 iPhone / iPad", callback_data=f"app_ios{suffix}"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🪟 Windows", callback_data=f"app_windows{suffix}"
                )
            ],
            [InlineKeyboardButton(text="🐧 Linux", callback_data=f"app_linux{suffix}")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)],
        ]
    )


def platform_keyboard(
    platform: str,
    url: str | None,
    *,
    back_callback: str = "back_to_devices",
) -> InlineKeyboardMarkup:
    if back_callback not in {
        "back_to_devices",
        "back_to_devices_main",
        "back_to_devices_subscription",
    }:
        raise ValueError("Unsupported platform navigation destination")
    rows: list[list[InlineKeyboardButton]] = []
    download = _safe_download_button(platform, url)
    if download is not None:
        rows.append([download])
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="🔑 Показать мой ключ", callback_data="subscription_link"
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=back_callback)],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def back_keyboard(destination: str) -> InlineKeyboardMarkup:
    allowed = {"back_to_key", "back_to_main"}
    if destination not in allowed:
        raise ValueError("Unsupported navigation destination")
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data=destination)]
        ]
    )


def subscription_menu(
    *,
    state: str = "active",
    trial_available: bool = False,
    has_key: bool = True,
    back_callback: str = "main_menu",
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if state == "none":
        if trial_available:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🎁 Попробовать бесплатно",
                        callback_data="activate_trial",
                    )
                ]
            )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="💳 Купить подписку",
                        callback_data="buy_vpn",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="📦 Посмотреть тарифы",
                        callback_data="tariffs",
                    )
                ],
            ]
        )
    elif state == "expired":
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Возобновить подписку",
                    callback_data="tariffs",
                )
            ]
        )
        if has_key:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔑 Показать прежний ключ",
                        callback_data="subscription_link",
                    )
                ]
            )
    elif state == "disabled":
        rows.append(
            [
                InlineKeyboardButton(
                    text="💳 Купить подписку на 30 дней",
                    callback_data="tariffs",
                )
            ]
        )
    else:
        if has_key:
            rows.append(
                [
                    InlineKeyboardButton(
                        text="🔑 Показать мой ключ",
                        callback_data="subscription_link",
                    )
                ]
            )
        rows.extend(
            [
                [
                    InlineKeyboardButton(
                        text="📱 Скачать приложение",
                        callback_data="apps_from_subscription",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="💳 Продлить подписку",
                        callback_data="tariffs",
                    )
                ],
                [
                    InlineKeyboardButton(
                        text="🔄 Обновить информацию",
                        callback_data="subscription_refresh",
                    )
                ],
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="🆘 Поддержка",
                    callback_data="support_from_subscription",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=back_callback,
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
