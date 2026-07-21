from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Купить VPN", callback_data="buy_vpn")],
            [
                InlineKeyboardButton(
                    text="Моя подписка", callback_data="my_subscription"
                )
            ],
            [InlineKeyboardButton(text="Тарифы", callback_data="tariffs")],
            [
                InlineKeyboardButton(
                    text="Как подключиться", callback_data="how_to_connect"
                )
            ],
            [InlineKeyboardButton(text="Поддержка", callback_data="support")],
        ]
    )
