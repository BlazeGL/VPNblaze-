from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import OrderCallback, PromoCallback, TariffCallback
from app.database.models import Order, Payment, Tariff


def money(value: object, currency: str = "RUB") -> str:
    amount = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{amount} ₽" if currency == "RUB" else f"{amount} {currency}"


def show_price_in_button(tariff: Tariff) -> bool:
    """Keep legacy and not-yet-persisted tariff objects price-visible."""
    return getattr(tariff, "show_price_in_button", None) is not False


def tariff_button_text(tariff: Tariff, *, selected: bool = False) -> str:
    marker = "✅ " if selected else ""
    show_price = show_price_in_button(tariff)
    max_name_length = 42 if show_price else 58
    name = (
        tariff.name
        if len(tariff.name) <= max_name_length
        else f"{tariff.name[: max_name_length - 3]}..."
    )
    price = f" · {money(tariff.price, tariff.currency)}" if show_price else ""
    return f"{marker}{name}{price}"


def build_tariff_card(
    tariff: Tariff,
    tariffs: list[Tariff] | None = None,
) -> InlineKeyboardMarkup:
    rows: list[list[InlineKeyboardButton]] = []
    if tariffs and len(tariffs) > 1:
        for item in tariffs:
            rows.append(
                [
                    InlineKeyboardButton(
                        text=tariff_button_text(
                            item,
                            selected=item.id == tariff.id,
                        ),
                        callback_data=TariffCallback(
                            action="view",
                            tariff_id=item.id,
                        ).pack(),
                    )
                ]
            )
    rows.extend(
        [
            [
                InlineKeyboardButton(
                    text=f"💳 Купить за {money(tariff.price, tariff.currency)}",
                    callback_data=TariffCallback(
                        action="buy", tariff_id=tariff.id
                    ).pack(),
                )
            ],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")],
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_order(order: Order) -> InlineKeyboardMarkup:
    order_id = str(order.id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"💰 Купить с баланса за {money(order.original_amount)}",
                    callback_data=OrderCallback(
                        action="balance", order_id=order_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="🎟 Применить промокод",
                    callback_data=PromoCallback(
                        action="apply", order_id=order_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Оплатить через ЮKassa",
                    callback_data=OrderCallback(action="pay", order_id=order_id).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="Отменить заказ",
                    callback_data=OrderCallback(
                        action="cancel", order_id=order_id
                    ).pack(),
                )
            ],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")],
        ]
    )


def build_insufficient_funds(
    order: Order, shortfall: object
) -> InlineKeyboardMarkup:
    order_id = str(order.id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text=f"➕ Пополнить на {money(shortfall)}",
                    callback_data=OrderCallback(
                        action="topup_shortfall", order_id=order_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="💳 Выбрать другую сумму",
                    callback_data=OrderCallback(
                        action="topup_other", order_id=order_id
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Назад",
                    callback_data="tariffs",
                )
            ],
        ]
    )


def build_payment(payment: Payment) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [InlineKeyboardButton(text="💳 Оплатить", url=payment.payment_url)],
            [
                InlineKeyboardButton(
                    text="🔄 Проверить оплату",
                    callback_data=OrderCallback(
                        action="check", order_id=str(payment.order_id)
                    ).pack(),
                )
            ],
            [
                InlineKeyboardButton(
                    text="❌ Отменить заказ",
                    callback_data=OrderCallback(
                        action="cancel", order_id=str(payment.order_id)
                    ).pack(),
                )
            ],
        ]
    )
