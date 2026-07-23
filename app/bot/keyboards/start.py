from urllib.parse import quote, urlsplit

from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.keyboards.subscription import SUPPORT_URL

START_CONNECTION_CALLBACK = "start_connection"
ACTIVATE_TRIAL_CALLBACK = "activate_trial"
BUY_SUBSCRIPTION_CALLBACK = "buy_vpn"
TARIFFS_CALLBACK = "tariffs"
MY_SUBSCRIPTION_CALLBACK = "my_subscription"
MAIN_MENU_CALLBACK = "main_menu"
BACK_TO_MAIN_CALLBACK = "back_to_main"
USER_AGREEMENT_CALLBACK = "legal_user_agreement"
PRIVACY_POLICY_CALLBACK = "legal_privacy_policy"
REFUND_TERMS_CALLBACK = "legal_refund_terms"
BONUSES_CALLBACK = "bonuses"
COPY_REFERRAL_LINK_CALLBACK = "copy_referral_link"


def is_valid_agreement_url(url: str | None) -> bool:
    if not url or any(character.isspace() for character in url):
        return False
    parsed = urlsplit(url)
    return parsed.scheme == "https" and bool(parsed.netloc)


def agreement_button(url: str | None = None) -> InlineKeyboardButton:
    if is_valid_agreement_url(url):
        return InlineKeyboardButton(
            text="📄 Пользовательское соглашение",
            url=url,
        )
    return InlineKeyboardButton(
        text="📄 Пользовательское соглашение",
        callback_data=USER_AGREEMENT_CALLBACK,
    )


def build_main_menu(
    user_agreement_url: str | None = None,
    *,
    show_bonuses: bool = False,
    support_url: str = SUPPORT_URL,
) -> InlineKeyboardMarkup:
    rows = [
            [
                InlineKeyboardButton(
                    text="🚀 Начать подключение",
                    callback_data=START_CONNECTION_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Купить подписку",
                    callback_data=BUY_SUBSCRIPTION_CALLBACK,
                ),
                InlineKeyboardButton(
                    text="📦 Тарифы",
                    callback_data=TARIFFS_CALLBACK,
                ),
            ],
            [
                InlineKeyboardButton(text="❓ Помощь", url=support_url),
                InlineKeyboardButton(
                    text="👤 Личный кабинет",
                    callback_data=MY_SUBSCRIPTION_CALLBACK,
                ),
            ],
        ]
    if show_bonuses:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎁 Бонусы",
                    callback_data=BONUSES_CALLBACK,
                )
            ]
        )
    rows.extend(
        [
            [agreement_button(user_agreement_url)],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def bonuses_menu(referral_link: str | None = None) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text="📋 Получить ссылку",
                callback_data=COPY_REFERRAL_LINK_CALLBACK,
            )
        ]
    ]
    if referral_link:
        rows.append(
            [
                InlineKeyboardButton(
                    text="📤 Поделиться",
                    url=(
                        "https://t.me/share/url?url="
                        f"{quote(referral_link, safe='')}"
                    ),
                )
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(
                text="⬅️ Главное меню",
                callback_data=MAIN_MENU_CALLBACK,
            )
        ]
    )
    return InlineKeyboardMarkup(
        inline_keyboard=rows
    )


def build_connection_menu(
    *,
    trial_available: bool,
    has_subscription: bool,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if trial_available:
        rows.append(
            [
                InlineKeyboardButton(
                    text="🎁 Получить бесплатный период",
                    callback_data=ACTIVATE_TRIAL_CALLBACK,
                )
            ]
        )
    if has_subscription:
        rows.append(
            [
                InlineKeyboardButton(
                    text="👤 Моя подписка",
                    callback_data=MY_SUBSCRIPTION_CALLBACK,
                )
            ]
        )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="💳 Купить подписку",
                    callback_data=BUY_SUBSCRIPTION_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=MAIN_MENU_CALLBACK,
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def agreement_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔒 Политика конфиденциальности",
                    callback_data=PRIVACY_POLICY_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Условия возврата",
                    callback_data=REFUND_TERMS_CALLBACK,
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=BACK_TO_MAIN_CALLBACK,
                )
            ],
        ]
    )


def legal_page_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=USER_AGREEMENT_CALLBACK,
                )
            ]
        ]
    )
