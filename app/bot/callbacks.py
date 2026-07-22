from aiogram.filters.callback_data import CallbackData


class TariffCallback(CallbackData, prefix="tariff"):
    action: str
    tariff_id: int


class OrderCallback(CallbackData, prefix="order"):
    action: str
    order_id: str


class AdminCallback(CallbackData, prefix="admin"):
    action: str
    tariff_id: int = 0


class PromoCallback(CallbackData, prefix="promo"):
    action: str
    order_id: str = ""


class PromoAdminCallback(CallbackData, prefix="apromo"):
    action: str
    value: str = ""
