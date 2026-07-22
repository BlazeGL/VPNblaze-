from datetime import UTC, datetime
from decimal import Decimal, InvalidOperation

from aiogram import F, Router
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import PromoAdminCallback
from app.bot.filters import AdminFilter
from app.bot.keyboards.admin import (
    promo_admin_menu,
    promo_confirm_keyboard,
    promo_scope_keyboard,
    promo_type_keyboard,
)
from app.database.models import PromoCode, PromoDiscountType, Tariff
from app.database.repositories import UserRepository
from app.services.promos import (
    PromoService,
    PromoValidationError,
    validate_code_format,
)

router = Router(name=__name__)


class PromoForm(StatesGroup):
    code = State()
    discount_type = State()
    value = State()
    scope = State()
    tariff_ids = State()
    max_uses = State()
    per_user_limit = State()
    minimum_amount = State()
    valid_from = State()
    valid_until = State()
    confirm = State()


async def begin_form(
    event: Message | CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    telegram_user = event.from_user
    async with session_factory() as session, session.begin():
        user, _ = await UserRepository(session).get_or_create(
            telegram_user.id,
            telegram_user.username,
            telegram_user.first_name,
            telegram_user.last_name,
            is_admin=True,
        )
        user.is_admin = True
        admin_user_id = user.id
    await state.clear()
    await state.update_data(admin_user_id=admin_user_id)
    await state.set_state(PromoForm.code)
    target = event if isinstance(event, Message) else event.message
    if target:
        await target.answer("Введите код промокода:")


@router.message(Command("new_promo"), AdminFilter())
async def new_promo_command(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await begin_form(message, state, session_factory)


@router.message(Command("new_promo"))
async def reject_new_promo(message: Message) -> None:
    await message.answer("⛔ У вас нет доступа к панели управления.")


@router.callback_query(PromoAdminCallback.filter(F.action == "new"), AdminFilter())
async def new_promo_callback(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await callback.answer()
    await begin_form(callback, state, session_factory)


@router.callback_query(PromoAdminCallback.filter(F.action == "list"), AdminFilter())
async def list_promos(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        count = await session.scalar(select(func.count(PromoCode.id))) or 0
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            f"Промокоды: {count}", reply_markup=promo_admin_menu()
        )


@router.callback_query(PromoAdminCallback.filter(F.action == "cancel"), AdminFilter())
async def cancel_promo_form(callback: CallbackQuery, state: FSMContext) -> None:
    await state.clear()
    await callback.answer("Создание отменено")
    if callback.message:
        await callback.message.edit_text(
            "Создание промокода отменено.", reply_markup=promo_admin_menu()
        )


@router.message(PromoForm.code, AdminFilter())
async def promo_code_step(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        code = validate_code_format(message.text or "")
    except PromoValidationError:
        await message.answer(
            "Код: 2–64 символа, только буквы, цифры, дефис и подчёркивание."
        )
        return
    async with session_factory() as session:
        duplicate = await PromoService(session).get_by_code(code)
    if duplicate is not None:
        await message.answer("Промокод с таким кодом уже существует.")
        return
    await state.update_data(code=code)
    await state.set_state(PromoForm.discount_type)
    await message.answer("Выберите тип промокода:", reply_markup=promo_type_keyboard())


@router.callback_query(
    PromoForm.discount_type,
    PromoAdminCallback.filter(F.action == "type"),
    AdminFilter(),
)
async def promo_type_step(
    callback: CallbackQuery,
    callback_data: PromoAdminCallback,
    state: FSMContext,
) -> None:
    try:
        discount_type = PromoDiscountType(callback_data.value)
    except ValueError:
        await callback.answer("Неизвестный тип", show_alert=True)
        return
    await state.update_data(discount_type=discount_type.value)
    await state.set_state(PromoForm.value)
    await callback.answer()
    if callback.message:
        prompt = (
            "Введите количество дополнительных дней:"
            if discount_type == PromoDiscountType.bonus_days
            else "Введите размер скидки:"
        )
        await callback.message.answer(prompt)


@router.message(PromoForm.value, AdminFilter())
async def promo_value_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    discount_type = PromoDiscountType(data["discount_type"])
    try:
        value = Decimal((message.text or "").replace(",", "."))
        if value <= 0 or value.as_tuple().exponent < -2:
            raise InvalidOperation
        if discount_type == PromoDiscountType.percent and value > 100:
            raise InvalidOperation
        if (
            discount_type == PromoDiscountType.bonus_days
            and value != value.to_integral()
        ):
            raise InvalidOperation
    except InvalidOperation:
        await message.answer(
            "Введите положительное значение; процент — от 1 до 100, дни — целое число."
        )
        return
    await state.update_data(
        discount_value=str(value),
        bonus_days=int(value)
        if discount_type == PromoDiscountType.bonus_days
        else None,
    )
    await state.set_state(PromoForm.scope)
    await message.answer("Выберите тарифы:", reply_markup=promo_scope_keyboard())


@router.callback_query(
    PromoForm.scope,
    PromoAdminCallback.filter(F.action == "scope"),
    AdminFilter(),
)
async def promo_scope_step(
    callback: CallbackQuery,
    callback_data: PromoAdminCallback,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await callback.answer()
    if callback_data.value == "all":
        await state.update_data(tariff_ids=None)
        await state.set_state(PromoForm.max_uses)
        if callback.message:
            await callback.message.answer(
                "Максимальное количество использований или «без ограничений»:"
            )
        return
    async with session_factory() as session:
        tariffs = list(
            await session.scalars(select(Tariff).order_by(Tariff.sort_order, Tariff.id))
        )
    if not tariffs:
        await callback.answer("Тарифы не найдены", show_alert=True)
        return
    await state.set_state(PromoForm.tariff_ids)
    if callback.message:
        listing = "\n".join(f"{item.id}: {item.name}" for item in tariffs)
        await callback.message.answer(f"Введите ID тарифов через запятую:\n{listing}")


@router.message(PromoForm.tariff_ids, AdminFilter())
async def promo_tariffs_step(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        tariff_ids = sorted(
            {int(item.strip()) for item in (message.text or "").split(",")}
        )
        if not tariff_ids or any(item <= 0 for item in tariff_ids):
            raise ValueError
    except ValueError:
        await message.answer("Введите существующие ID тарифов через запятую.")
        return
    async with session_factory() as session:
        found = set(
            await session.scalars(select(Tariff.id).where(Tariff.id.in_(tariff_ids)))
        )
    if found != set(tariff_ids):
        await message.answer("Один или несколько тарифов не найдены.")
        return
    await state.update_data(tariff_ids=tariff_ids)
    await state.set_state(PromoForm.max_uses)
    await message.answer("Максимальное количество использований или «без ограничений»:")


@router.message(PromoForm.max_uses, AdminFilter())
async def promo_max_uses_step(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    if text in {"без ограничений", "-", "нет"}:
        value = None
    else:
        try:
            value = int(text)
            if value <= 0:
                raise ValueError
        except ValueError:
            await message.answer("Введите целое число больше 0 или «без ограничений».")
            return
    await state.update_data(max_uses=value)
    await state.set_state(PromoForm.per_user_limit)
    await message.answer("Лимит использований на одного пользователя:")


@router.message(PromoForm.per_user_limit, AdminFilter())
async def promo_per_user_step(message: Message, state: FSMContext) -> None:
    try:
        value = int(message.text or "")
        if value <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Введите целое число больше 0.")
        return
    await state.update_data(per_user_limit=value)
    await state.set_state(PromoForm.minimum_amount)
    await message.answer("Минимальная сумма заказа или «-», чтобы пропустить:")


@router.message(PromoForm.minimum_amount, AdminFilter())
async def promo_minimum_step(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip()
    if text == "-":
        value = None
    else:
        try:
            parsed = Decimal(text.replace(",", "."))
            if parsed < 0 or parsed.as_tuple().exponent < -2:
                raise InvalidOperation
            value = str(parsed)
        except InvalidOperation:
            await message.answer("Введите неотрицательную сумму с точностью до копеек.")
            return
    await state.update_data(minimum_order_amount=value)
    await state.set_state(PromoForm.valid_from)
    await message.answer("Дата начала в UTC (ДД.ММ.ГГГГ ЧЧ:ММ) или «сразу»:")


def parse_datetime(value: str) -> datetime:
    return datetime.strptime(value.strip(), "%d.%m.%Y %H:%M").replace(tzinfo=UTC)


@router.message(PromoForm.valid_from, AdminFilter())
async def promo_valid_from_step(message: Message, state: FSMContext) -> None:
    text = (message.text or "").strip().lower()
    if text in {"сразу", "-"}:
        value = None
    else:
        try:
            value = parse_datetime(text).isoformat()
        except ValueError:
            await message.answer("Используйте формат ДД.ММ.ГГГГ ЧЧ:ММ или «сразу».")
            return
    await state.update_data(valid_from=value)
    await state.set_state(PromoForm.valid_until)
    await message.answer("Дата окончания в UTC (ДД.ММ.ГГГГ ЧЧ:ММ) или «бессрочно»:")


@router.message(PromoForm.valid_until, AdminFilter())
async def promo_valid_until_step(message: Message, state: FSMContext) -> None:
    data = await state.get_data()
    text = (message.text or "").strip().lower()
    if text in {"бессрочно", "-"}:
        value = None
    else:
        try:
            parsed = parse_datetime(text)
            start = (
                datetime.fromisoformat(data["valid_from"])
                if data.get("valid_from")
                else datetime.now(UTC)
            )
            if parsed <= start:
                raise ValueError
            value = parsed.isoformat()
        except ValueError:
            await message.answer("Дата окончания должна быть позже даты начала.")
            return
    await state.update_data(valid_until=value)
    data = await state.get_data()
    discount_type = PromoDiscountType(data["discount_type"])
    type_text = {
        PromoDiscountType.percent: f"скидка {data['discount_value']}%",
        PromoDiscountType.fixed: f"скидка {data['discount_value']} ₽",
        PromoDiscountType.bonus_days: f"+{data['bonus_days']} дней",
    }[discount_type]
    until = (
        datetime.fromisoformat(value).strftime("%d.%m.%Y %H:%M UTC")
        if value
        else "бессрочно"
    )
    await state.set_state(PromoForm.confirm)
    await message.answer(
        "Подтвердите создание:\n\n"
        f"Код: {data['code']}\n"
        f"Тип: {type_text}\n"
        f"Максимум использований: {data['max_uses'] or 'без ограничений'}\n"
        f"Лимит на пользователя: {data['per_user_limit']}\n"
        f"Действует до: {until}",
        reply_markup=promo_confirm_keyboard(),
    )


@router.callback_query(
    PromoForm.confirm,
    PromoAdminCallback.filter(F.action == "create"),
    AdminFilter(),
)
async def create_promo(
    callback: CallbackQuery,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    data = await state.get_data()
    discount_type = PromoDiscountType(data["discount_type"])
    try:
        async with session_factory() as session, session.begin():
            promo = await PromoService(session).create(
                code=data["code"],
                discount_type=discount_type,
                discount_value=Decimal(data["discount_value"]),
                bonus_days=data.get("bonus_days"),
                max_uses=data.get("max_uses"),
                per_user_limit=data["per_user_limit"],
                minimum_order_amount=(
                    Decimal(data["minimum_order_amount"])
                    if data.get("minimum_order_amount") is not None
                    else None
                ),
                valid_from=(
                    datetime.fromisoformat(data["valid_from"])
                    if data.get("valid_from")
                    else None
                ),
                valid_until=(
                    datetime.fromisoformat(data["valid_until"])
                    if data.get("valid_until")
                    else None
                ),
                created_by_admin_id=data["admin_user_id"],
                tariff_ids=data.get("tariff_ids"),
                actor_telegram_id=callback.from_user.id,
            )
    except PromoValidationError as exc:
        await callback.answer(f"Не удалось создать: {exc.reason}", show_alert=True)
        return
    await state.clear()
    type_text = {
        PromoDiscountType.percent: f"скидка {promo.discount_value.normalize()}%",
        PromoDiscountType.fixed: f"скидка {promo.discount_value.normalize()} ₽",
        PromoDiscountType.bonus_days: f"+{promo.bonus_days} дней",
    }[promo.discount_type]
    until = (
        promo.valid_until.strftime("%d.%m.%Y %H:%M UTC")
        if promo.valid_until
        else "бессрочно"
    )
    await callback.answer("Промокод создан")
    if callback.message:
        await callback.message.edit_text(
            "✅ Промокод создан\n\n"
            f"Код: {promo.code}\n"
            f"Тип: {type_text}\n"
            f"Использований: 0/{promo.max_uses or '∞'}\n"
            f"Лимит на пользователя: {promo.per_user_limit}\n"
            f"Действует до: {until}",
            reply_markup=promo_admin_menu(),
        )


@router.callback_query(PromoAdminCallback.filter())
async def reject_promo_admin_callback(callback: CallbackQuery) -> None:
    await callback.answer("⛔ Нет доступа", show_alert=True)
