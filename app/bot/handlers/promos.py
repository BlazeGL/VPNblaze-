import uuid
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType
from aiogram.filters import BaseFilter, StateFilter
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import PromoCallback
from app.bot.keyboards.start import TARIFFS_CALLBACK
from app.bot.keyboards.tariffs import build_order, money
from app.bot.promo_context import (
    clear_pending_promo_code,
    promo_error_message,
    remember_pending_promo_code,
)
from app.core.config import Settings
from app.database.models import Order, OrderPurpose
from app.database.repositories import OrderRepository, UserRepository
from app.services.promos import (
    PromoApplication,
    PromoService,
    PromoValidationError,
    validate_code_format,
)

router = Router(name=__name__)


class PromoInput(StatesGroup):
    code = State()


def promo_input_keyboard() -> InlineKeyboardMarkup:
    return InlineKeyboardMarkup(
        inline_keyboard=[
            [
                InlineKeyboardButton(
                    text="❌ Отменить",
                    callback_data="promo_input_cancel",
                )
            ],
            [
                InlineKeyboardButton(
                    text="⬅️ Главное меню",
                    callback_data="promo_input_main",
                )
            ],
        ]
    )


@router.callback_query(F.data == "promo_enter")
async def enter_promo_from_menu(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await state.clear()
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(callback.from_user.id)
        order = (
            await OrderRepository(session).get_latest_pending_for_user(
                user.id,
                purpose=OrderPurpose.subscription_purchase,
            )
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
        await callback.message.answer(
            "🎟 <b>Активация промокода</b>\n\n"
            "Отправьте промокод следующим сообщением.",
            reply_markup=promo_input_keyboard(),
            parse_mode="HTML",
        )


@router.callback_query(F.data == "promo_input_cancel")
async def cancel_promo_input(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Ввод промокода отменён.")
    if callback.message:
        await callback.message.answer("Ввод промокода отменён.")


@router.callback_query(F.data == "promo_input_main")
async def cancel_promo_to_main(
    callback: CallbackQuery,
    state: FSMContext,
    settings: Settings,
) -> None:
    from app.bot.handlers.start import send_welcome

    await state.clear()
    await callback.answer()
    if callback.message:
        await send_welcome(
            callback.message,
            settings.user_agreement_url,
            settings.support_url,
        )


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


class PromoCodeTextFilter(BaseFilter):
    async def __call__(
        self,
        message: Message,
        session_factory: async_sessionmaker[AsyncSession],
    ) -> bool | dict[str, str]:
        if (
            message.chat.type != ChatType.PRIVATE
            or message.from_user is None
            or message.text is None
        ):
            return False
        try:
            promo_code = validate_code_format(message.text)
        except PromoValidationError:
            return False
        async with session_factory() as session:
            promo = await PromoService(session).get_by_code(promo_code)
        if promo is None:
            return False
        return {"promo_code": promo_code}


def _promo_description(application: PromoApplication) -> str:
    promo = application.promo_code
    if application.bonus_days:
        return f"+{application.bonus_days} дней к подписке"
    label = (
        f"-{promo.discount_value.normalize()}%"
        if promo.discount_type.value == "percent"
        else f"-{money(promo.discount_value)}"
    )
    return (
        f"{label}\n"
        f"Старая цена: {money(application.original_amount)}\n"
        f"Скидка: {money(application.discount_amount)}\n"
        f"К оплате: {money(application.final_amount)}"
    )


async def _send_promo_success(
    message: Message,
    order: Order,
    application: PromoApplication,
) -> None:
    await message.answer(
        "✅ <b>Промокод применён</b>\n\n" + _promo_description(application),
        reply_markup=build_order(order),
        parse_mode="HTML",
    )


@router.message(StateFilter(None), PromoCodeTextFilter())
async def apply_promo_from_any_screen(
    message: Message,
    promo_code: str,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if message.from_user is None:
        return
    order: Order | None = None
    application: PromoApplication | None = None
    user_exists = True
    try:
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                message.from_user.id
            )
            if user is None:
                user_exists = False
            else:
                order = await OrderRepository(session).get_latest_pending_for_user(
                    user.id,
                    purpose=OrderPurpose.subscription_purchase,
                    for_update=True,
                )
                if order is not None:
                    application = await PromoService(session).apply_to_order(
                        order,
                        user_id=user.id,
                        code=promo_code,
                        actor_telegram_id=message.from_user.id,
                    )
    except PromoValidationError as exc:
        await clear_pending_promo_code(state)
        await message.answer(promo_error_message(exc.reason))
        return

    if not user_exists:
        await message.answer("Сначала нажмите /start.")
        return
    if order is None or application is None:
        await remember_pending_promo_code(state, promo_code)
        await message.answer(
            "🎟 <b>Промокод сохранён</b>\n\n"
            f"Код <code>{escape(promo_code)}</code> применится автоматически "
            "после выбора тарифа.",
            reply_markup=InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="💳 Выбрать тариф",
                            callback_data=TARIFFS_CALLBACK,
                        )
                    ]
                ]
            ),
            parse_mode="HTML",
        )
        return

    await clear_pending_promo_code(state)
    await _send_promo_success(message, order, application)


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
            promo_error_message(reason)
        )
        return
    await state.clear()
    await _send_promo_success(message, order, application)
