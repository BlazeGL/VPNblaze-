from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import AdminCallback, PromoAdminCallback
from app.database.models import Tariff


def admin_menu() -> InlineKeyboardMarkup:
    items = [
        ("📊 Статистика", "stats_v3"),
        ("💳 Тарифы", "tariffs"),
        ("🧾 Заказы", "orders"),
        ("💰 Платежи", "payments"),
        ("🎟 Промокоды", "promos"),
        ("👥 Пользователи", "users"),
        ("🌐 Remnawave", "remnawave"),
        ("📢 Рассылка", "broadcast"),
        ("⚙️ Настройки", "settings"),
        ("❌ Закрыть панель", "close"),
    ]
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=text, callback_data=AdminCallback(action=action).pack()
                )
            ]
            for text, action in items
        ]
    )


def remnawave_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Проверить подключение", callback_data="rw:check"
                )
            ],
            [InlineKeyboardButton(text="Синхронизировать", callback_data="rw:sync")],
            [
                InlineKeyboardButton(
                    text="Повторить активации", callback_data="rw:retry"
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад", callback_data=AdminCallback(action="menu").pack()
                )
            ],
        ]
    )


def admin_tariffs(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{'✅' if item.is_active else '⛔'} {item.name}",
                callback_data=AdminCallback(action="edit", tariff_id=item.id).pack(),
            )
        ]
        for item in tariffs
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Создать тариф",
                    callback_data=AdminCallback(action="create").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад", callback_data=AdminCallback(action="menu").pack()
                )
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tariff_actions(tariff: Tariff) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Изменить",
                    callback_data=AdminCallback(
                        action="form", tariff_id=tariff.id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Выключить" if tariff.is_active else "Включить",
                    callback_data=AdminCallback(
                        action="toggle", tariff_id=tariff.id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="К тарифам",
                    callback_data=AdminCallback(action="tariffs").pack(),
                )
            ],
        ]
    )


def confirm_form() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Сохранить", callback_data=AdminCallback(action="save").pack()
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отмена", callback_data=AdminCallback(action="tariffs").pack()
                )
            ],
        ]
    )


def promo_type_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Процентная скидка",
                    callback_data=PromoAdminCallback(
                        action="type", value="percent"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Фиксированная скидка",
                    callback_data=PromoAdminCallback(
                        action="type", value="fixed"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Дополнительные дни",
                    callback_data=PromoAdminCallback(
                        action="type", value="bonus_days"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=PromoAdminCallback(action="cancel").pack(),
                )
            ],
        ]
    )


def promo_scope_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Все тарифы",
                    callback_data=PromoAdminCallback(
                        action="scope", value="all"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Конкретные тарифы",
                    callback_data=PromoAdminCallback(
                        action="scope", value="specific"
                    ).pack(),
                )
            ],
        ]
    )


def promo_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Создать",
                    callback_data=PromoAdminCallback(action="create").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отменить",
                    callback_data=PromoAdminCallback(action="cancel").pack(),
                )
            ],
        ]
    )


def promo_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Создать промокод",
                    callback_data=PromoAdminCallback(action="new").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Назад", callback_data=AdminCallback(action="menu").pack()
                )
            ],
        ]
    )
