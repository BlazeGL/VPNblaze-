from aiogram.fsm.context import FSMContext

PENDING_PROMO_CODE_KEY = "pending_promo_code"

ERROR_MESSAGES = {
    "not_found": "Промокод не найден.",
    "inactive": "Промокод отключён.",
    "not_started": "Промокод ещё не действует.",
    "expired": "Срок действия промокода истёк.",
    "max_uses_reached": "Лимит использований промокода исчерпан.",
    "per_user_limit_reached": "Вы уже использовали этот промокод.",
    "minimum_amount": "Сумма заказа меньше минимальной для этого промокода.",
    "tariff_not_applicable": "Промокод не действует для выбранного тарифа.",
    "order_status": "Промокод уже нельзя применить к этому заказу.",
    "bonus_days_direct_only": (
        "Промокод на дополнительные дни активируется без покупки. "
        "Отправьте его ещё раз обычным сообщением."
    ),
    "subscription_required": (
        "Для этого промокода нужна существующая подписка. Сначала активируйте "
        "бесплатный период или оформите подписку."
    ),
}


def promo_error_message(reason: str) -> str:
    return ERROR_MESSAGES.get(reason, "Промокод применить нельзя.")


async def get_pending_promo_code(state: FSMContext) -> str | None:
    value = (await state.get_data()).get(PENDING_PROMO_CODE_KEY)
    return value if isinstance(value, str) and value else None


async def remember_pending_promo_code(state: FSMContext, code: str) -> None:
    await state.update_data(**{PENDING_PROMO_CODE_KEY: code})


async def clear_pending_promo_code(state: FSMContext) -> None:
    data = await state.get_data()
    if PENDING_PROMO_CODE_KEY not in data:
        return
    data.pop(PENDING_PROMO_CODE_KEY)
    await state.set_data(data)
