import uuid

from aiogram import F, Router
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import PromoCallback
from app.bot.keyboards.tariffs import build_order, money
from app.database.models import Order
from app.database.repositories import OrderRepository, UserRepository
from app.services.promos import PromoService, PromoValidationError

router = Router(name=__name__)


class PromoInput(StatesGroup):
    code = State()


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
}


@router.callback_query(F.data == "promo_enter")
async def enter_promo_from_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(callback.from_user.id)
        order = (
            await OrderRepository(session).get_latest_pending_for_user(user.id)
            if user is not None
            else None
        )
    if order is None:
        await callback.answer()
        if callback.message:
            await callback.message.answer(
                "Сначала выберите тариф и создайте заказ, затем примените промокод."
            )
        return
    await state.set_state(PromoInput.code)
    await state.update_data(order_id=str(order.id))
    await callback.answer()
    if callback.message:
        await callback.message.answer("Введите промокод:")


@router.callback_query(PromoCallback.filter(F.action == "apply"))
async def enter_promo_for_order(
    callback: CallbackQuery,
    callback_data: PromoCallback,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(callback.from_user.id)
        order = await OrderRepository(session).get_by_id(callback_data.order_id)
    if user is None or order is None or order.user_id != user.id:
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await state.set_state(PromoInput.code)
    await state.update_data(order_id=str(order.id))
    await callback.answer()
    if callback.message:
        await callback.message.answer("Введите промокод:")


@router.message(PromoInput.code)
async def apply_promo(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if not message.text:
        await message.answer("Введите промокод текстом.")
        return
    data = await state.get_data()
    try:
        order_id = uuid.UUID(str(data.get("order_id")))
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                message.from_user.id
            )
            order = await session.scalar(
                select(Order).where(Order.id == order_id).with_for_update()
            )
            if user is None or order is None or order.user_id != user.id:
                raise PromoValidationError("foreign_order")
            application = await PromoService(session).apply_to_order(
                order,
                user_id=user.id,
                code=message.text,
                actor_telegram_id=message.from_user.id,
            )
    except (ValueError, PromoValidationError) as exc:
        reason = (
            exc.reason if isinstance(exc, PromoValidationError) else "foreign_order"
        )
        await message.answer(
            ERROR_MESSAGES.get(reason, "Промокод применить нельзя.")
        )
        return
    await state.clear()
    promo = application.promo_code
    if application.bonus_days:
        text = f"Промокод применён: +{application.bonus_days} дней"
    else:
        label = (
            f"-{promo.discount_value.normalize()}%"
            if promo.discount_type.value == "percent"
            else f"-{money(promo.discount_value)}"
        )
        text = (
            f"Промокод применён: {label}\n"
            f"Старая цена: {money(application.original_amount)}\n"
            f"Скидка: {money(application.discount_amount)}\n"
            f"К оплате: {money(application.final_amount)}"
        )
    await message.answer(text, reply_markup=build_order(order))
