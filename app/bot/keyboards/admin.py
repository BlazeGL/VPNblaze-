from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import AdminCallback, PromoAdminCallback
from app.bot.keyboards.tariffs import money
from app.database.models import Tariff


def _admin_button(
    text: str, action: str, *, tariff_id: int = 0
) -> InlineKeyboardButton:
    return InlineKeyboardButton(
        text=text,
        callback_data=AdminCallback(action=action, tariff_id=tariff_id).pack(),
    )


def _back_home_row(back_action: str) -> list[InlineKeyboardButton]:
    return [
        _admin_button("⬅️ Назад", back_action),
        _admin_button("🏠 Главное меню", "menu"),
    ]


def _home_row() -> list[InlineKeyboardButton]:
    return [_admin_button("🏠 Главное меню", "menu")]


def admin_navigation(back_action: str = "menu") -> InlineKeyboardMarkup:
    """Единая навигация для экранов панели администратора."""
    row = _home_row() if back_action == "menu" else _back_home_row(back_action)
    return InlineKeyboardMarkup(inline_keyboard=[row])


def _sorted_tariffs(tariffs: list[Tariff]) -> list[Tariff]:
    return sorted(tariffs, key=lambda item: (item.sort_order, item.id))


def _tariff_button_name(tariff: Tariff) -> str:
    return tariff.name if len(tariff.name) <= 30 else f"{tariff.name[:27]}..."


def admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _admin_button("💳 Тарифы и продажи", "tariffs"),
                _admin_button("📣 Рассылка", "broadcast"),
            ],
            [
                _admin_button("👥 Пользователи", "users_section"),
                _admin_button("🎟 Промокоды", "promos"),
            ],
            [
                _admin_button("🌐 VPN-доступ", "remnawave"),
                _admin_button("✖️ Закрыть", "close"),
            ],
        ]
    )


def admin_overview_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(inline_keyboard=[_home_row()])


def admin_users_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _admin_button("🔎 Найти пользователя", "user_search"),
                InlineKeyboardButton(
                    text="🎁 Выдать VPN-доступ",
                    callback_data="rw:grant:users",
                ),
            ],
            [
                _admin_button("👥 Реферальная статистика", "ref_stats"),
                _admin_button("🧪 Пробный доступ", "trial_access"),
            ],
            _home_row(),
        ]
    )


def admin_sales_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _admin_button("🧾 Заказы", "orders"),
                _admin_button("💰 Платежи", "payments"),
            ],
            [_admin_button("🎟 Промокоды", "promos")],
            _back_home_row("tariffs"),
        ]
    )


def remnawave_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="🔌 Проверить связь", callback_data="rw:check"
                ),
                InlineKeyboardButton(
                    text="🔄 Обновить данные", callback_data="rw:sync"
                ),
            ],
            [
                InlineKeyboardButton(
                    text="♻️ Повторить ошибки", callback_data="rw:retry"
                ),
                InlineKeyboardButton(
                    text="🎁 Выдать VPN-доступ",
                    callback_data="rw:grant:vpn",
                ),
            ],
            _home_row(),
        ]
    )


def admin_tariffs(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{'✅' if item.is_active else '⏸'} "
                    f"{_tariff_button_name(item)}"
                    f" — {money(item.price, item.currency)}"
                ),
                callback_data=AdminCallback(
                    action="price",
                    tariff_id=item.id,
                ).pack(),
            )
        ]
        for item in _sorted_tariffs(tariffs)
    ]

    rows.extend(
        [
            [
                _admin_button("➕ Новый тариф", "create"),
                _admin_button("⚙️ Параметры", "tariff_management"),
            ],
            [_admin_button("🧾 Продажи", "sales")],
            _home_row(),
        ]
    )

    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tariff_management(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{'✅' if item.is_active else '⏸'} "
                    f"{_tariff_button_name(item)}"
                ),
                callback_data=AdminCallback(
                    action="edit",
                    tariff_id=item.id,
                ).pack(),
            )
        ]
        for item in _sorted_tariffs(tariffs)
    ]
    rows.extend(
        [
            _back_home_row("tariffs"),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def admin_tariff_actions(tariff: Tariff) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _admin_button("💰 Изменить цену", "price", tariff_id=tariff.id),
                _admin_button(
                    "✏️ Изменить параметры", "form", tariff_id=tariff.id
                ),
            ],
            [
                _admin_button(
                    "⏸ Скрыть тариф" if tariff.is_active else "▶️ Показать тариф",
                    "toggle",
                    tariff_id=tariff.id,
                )
            ],
            _back_home_row("tariff_management"),
        ]
    )


def admin_price_navigation() -> InlineKeyboardMarkup:
    return admin_navigation("tariffs")


def broadcast_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _admin_button("✅ Отправить всем", "broadcast_send"),
                _admin_button("✖️ Отменить", "menu"),
            ]
        ]
    )


def confirm_form() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                _admin_button("✅ Сохранить", "save"),
                _admin_button("✖️ Отменить", "tariff_management"),
            ],
            [_admin_button("🏠 Главное меню", "menu")],
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
                ),
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
                    text="⬅️ Назад",
                    callback_data=PromoAdminCallback(action="cancel").pack(),
                ),
                _admin_button("🏠 Главное меню", "menu"),
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
                ),
                InlineKeyboardButton(
                    text="Конкретные тарифы",
                    callback_data=PromoAdminCallback(
                        action="scope", value="specific"
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=PromoAdminCallback(action="cancel").pack(),
                ),
                _admin_button("🏠 Главное меню", "menu"),
            ],
        ]
    )


def promo_tariff_selection(
    tariffs: list[Tariff],
    selected_ids: set[int],
) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=(
                    f"{'✅' if item.id in selected_ids else '⬜'} "
                    f"{_tariff_button_name(item)}"
                ),
                callback_data=PromoAdminCallback(
                    action="tariff",
                    value=str(item.id),
                ).pack(),
            )
        ]
        for item in _sorted_tariffs(tariffs)
    ]
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="Продолжить",
                    callback_data=PromoAdminCallback(action="tariffs_done").pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=PromoAdminCallback(action="cancel").pack(),
                ),
                _admin_button("🏠 Главное меню", "menu"),
            ],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def promo_confirm_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="✅ Создать",
                    callback_data=PromoAdminCallback(action="create").pack(),
                ),
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data=PromoAdminCallback(action="cancel").pack(),
                ),
            ],
            [_admin_button("🏠 Главное меню", "menu")],
        ]
    )


def promo_admin_menu() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="➕ Создать",
                    callback_data=PromoAdminCallback(action="new").pack(),
                ),
                InlineKeyboardButton(
                    text="📋 Список",
                    callback_data=PromoAdminCallback(action="list").pack(),
                ),
            ],
            _home_row(),
        ]
    )


def promo_list_menu(page: int, total_pages: int) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    page_buttons: list[InlineKeyboardButton] = []
    if page > 0:
        page_buttons.append(
            InlineKeyboardButton(
                text="⬅️ Новее",
                callback_data=PromoAdminCallback(
                    action="list",
                    value=str(page - 1),
                ).pack(),
            )
        )
    if page + 1 < total_pages:
        page_buttons.append(
            InlineKeyboardButton(
                text="Старее ➡️",
                callback_data=PromoAdminCallback(
                    action="list",
                    value=str(page + 1),
                ).pack(),
            )
        )
    if page_buttons:
        rows.append(page_buttons)
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text="➕ Создать",
                    callback_data=PromoAdminCallback(action="new").pack(),
                )
            ],
            _home_row(),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)
