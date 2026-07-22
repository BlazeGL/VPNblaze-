from aiogram.types import InlineKeyboardButton, InlineKeyboardMarkup

from app.bot.callbacks import OrderCallback, PromoCallback, TariffCallback
from app.database.models import Order, Payment, Tariff


def money(value: object, currency: str = "RUB") -> str:
    amount = f"{value:.2f}".rstrip("0").rstrip(".")
    return f"{amount} ₽" if currency == "RUB" else f"{amount} {currency}"


def build_tariffs(tariffs: list[Tariff]) -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(
                text=f"{tariff.name} — {money(tariff.price, tariff.currency)}",
                callback_data=TariffCallback(action="view", tariff_id=tariff.id).pack(),
            )
        ]
        for tariff in tariffs
    ]
    rows.append([InlineKeyboardButton(text="Главное меню", callback_data="main_menu")])
    return InlineKeyboardMarkup(inline_keyboard=rows)


def build_tariff_card(tariff_id: int) -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="Купить",
                    callback_data=TariffCallback(
                        action="buy", tariff_id=tariff_id
                    ).pack(),
                )
            ],
            [InlineKeyboardButton(text="Назад к тарифам", callback_data="tariffs")],
            [InlineKeyboardButton(text="Главное меню", callback_data="main_menu")],
        ]
    )


def build_order(order: Order) -> InlineKeyboardMarkup:
    order_id = str(order.id)
    return InlineKeyboardMarkup(
        inline_keyboard=[
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
                    text="Перейти к оплате",
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
