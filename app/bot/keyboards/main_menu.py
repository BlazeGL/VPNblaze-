from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def build_main_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="Купить VPN", callback_data="buy_vpn")],
            [
                InlineKeyboardButton(
                    text="🎁 Получить 7 дней бесплатно", callback_data="activate_trial"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Ввести промокод", callback_data="promo_enter"
                )
            ],
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
