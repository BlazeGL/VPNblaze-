from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup


def subscription_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔗 Получить ссылку", callback_data="subscription_link"
                )
            ],
            [
                InlineKeyboardButton(
                    text="🔄 Обновить данные", callback_data="subscription_refresh"
                )
            ],
            [InlineKeyboardButton(text="💳 Продлить", callback_data="tariffs")],
            [
                InlineKeyboardButton(
                    text="📱 Как подключиться", callback_data="how_to_connect"
                )
            ],
            [InlineKeyboardButton(text="🆘 Поддержка", callback_data="support")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")],
        ]
    )
