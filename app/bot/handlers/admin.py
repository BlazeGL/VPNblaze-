from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import AdminCallback
from app.bot.filters import AdminFilter
from app.bot.keyboards.admin import (
    admin_menu,
    admin_tariff_actions,
    admin_tariffs,
    confirm_form,
    promo_admin_menu,
    remnawave_admin_menu,
)
from app.database.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    PromoCode,
    PromoCodeUsage,
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    Tariff,
    User,
)
from app.database.repositories import TariffRepository, UserRepository
from app.services.audit import add_audit_log
from app.services.referrals import ReferralService
from app.services.trials import TrialService

router = Router(name=__name__)


class TariffForm(StatesGroup):
    name = State()
    description = State()
    duration = State()
    price = State()
    currency = State()
    traffic = State()
    unlimited = State()
    devices = State()
    sort_order = State()
    active = State()
    confirm = State()


@router.message(Command("edik"), AdminFilter())
async def open_edik(message: Message) -> None:
    await message.answer("Панель администратора", reply_markup=admin_menu())


@router.message(Command("edik"))
async def reject_edik(message: Message) -> None:
    await message.answer("⛔ У вас нет доступа к панели управления.")


@router.message(Command(commands=["trial_off", "trial_on"]), AdminFilter())
async def set_trial_access(
    message: Message,
    command: CommandObject,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        target_telegram_id = int(command.args or "")
        if target_telegram_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer("Использование: /trial_off TELEGRAM_ID")
        return
    disabled = command.command == "trial_off"
    try:
        async with session_factory() as session, session.begin():
            actor = await UserRepository(session).get_by_telegram_id(
                message.from_user.id
            )
            await TrialService(session).set_disabled(
                target_telegram_id,
                disabled=disabled,
                actor_user_id=actor.id if actor else None,
                actor_telegram_id=message.from_user.id,
            )
    except LookupError:
        await message.answer("Пользователь не найден.")
        return
    await message.answer("Trial запрещён." if disabled else "Запрет на trial снят.")


@router.message(Command("admin"), AdminFilter())
async def open_admin(message: Message) -> None:
    await message.answer("Панель администратора", reply_markup=admin_menu())


@router.message(Command("ref_stats"), AdminFilter())
async def referral_stats(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        stats = await ReferralService(session).global_stats()
    leaders = "\n".join(
        (
            f"{index}. "
            f"{('@' + user.username) if user.username else user.telegram_id}"
            f" — {count}"
        )
        for index, (user, count) in enumerate(stats.top_referrers, start=1)
    )
    if not leaders:
        leaders = "Пока нет приглашений."
    await message.answer(
        "👥 Реферальная статистика\n\n"
        f"Всего рефералов: {stats.total_referrals}\n"
        f"Начислено: {stats.total_awarded:.2f} ₽\n\n"
        f"ТОП пригласивших:\n{leaders}"
    )


@router.message(Command("admin"))
async def reject_admin(message: Message) -> None:
    await message.answer("У вас нет доступа к этому разделу.")


@router.callback_query(AdminCallback.filter(), AdminFilter())
async def admin_actions(
    callback: CallbackQuery,
    callback_data: AdminCallback,
    session_factory: async_sessionmaker[AsyncSession],
    state: FSMContext,
) -> None:
    action = callback_data.action
    if action == "close":
        await state.clear()
        if callback.message:
            await callback.message.delete()
        await callback.answer()
        return
    if action == "menu":
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                "Панель администратора", reply_markup=admin_menu()
            )
    elif action == "tariffs":
        await state.clear()
        async with session_factory() as session:
            items = await TariffRepository(session).get_all()
        if callback.message:
            await callback.message.edit_text(
                "Все тарифы:", reply_markup=admin_tariffs(items)
            )
    elif action == "edit":
        async with session_factory() as session:
            item = await TariffRepository(session).get_by_id(callback_data.tariff_id)
        if item and callback.message:
            await callback.message.edit_text(
                f"{item.name}\n{item.duration_days} дней, "
                f"{item.price} {item.currency}\n"
                f"Активен: {'да' if item.is_active else 'нет'}",
                reply_markup=admin_tariff_actions(item),
            )
    elif action == "toggle":
        async with session_factory() as session, session.begin():
            repository = TariffRepository(session)
            item = await repository.get_by_id(callback_data.tariff_id)
            if item:
                item = await repository.update(item, is_active=not item.is_active)
                actor = await UserRepository(session).get_by_telegram_id(
                    callback.from_user.id
                )
                add_audit_log(
                    session,
                    action="admin_tariff_status_changed",
                    entity_type="tariff",
                    entity_id=item.id,
                    actor_user_id=actor.id if actor else None,
                    actor_telegram_id=callback.from_user.id,
                    details={"is_active": item.is_active},
                )
        if item and callback.message:
            await callback.message.edit_text(
                f"Статус тарифа «{item.name}» изменён.",
                reply_markup=admin_tariff_actions(item),
            )
    elif action in {"create", "form"}:
        await state.clear()
        await state.update_data(tariff_id=callback_data.tariff_id or None)
        await state.set_state(TariffForm.name)
        if callback.message:
            await callback.message.answer("Введите название тарифа:")
    elif action == "save":
        data = await state.get_data()
        values = {
            key: data[key]
            for key in (
                "name",
                "description",
                "duration_days",
                "price",
                "currency",
                "traffic_limit_gb",
                "is_unlimited_traffic",
                "device_limit",
                "sort_order",
                "is_active",
            )
        }
        async with session_factory() as session, session.begin():
            repository = TariffRepository(session)
            if data.get("tariff_id"):
                saved = await repository.update(data["tariff_id"], **values)
            else:
                saved = await repository.create(**values)
            actor = await UserRepository(session).get_by_telegram_id(
                callback.from_user.id
            )
            add_audit_log(
                session,
                action="admin_tariff_saved",
                entity_type="tariff",
                entity_id=saved.id,
                actor_user_id=actor.id if actor else None,
                actor_telegram_id=callback.from_user.id,
            )
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                "Тариф сохранён.", reply_markup=admin_menu()
            )
    elif action == "orders":
        async with session_factory() as session:
            count = await session.scalar(select(func.count(Order.id)))
        if callback.message:
            await callback.message.edit_text(
                f"Всего заказов: {count or 0}", reply_markup=admin_menu()
            )
    elif action == "payments":
        async with session_factory() as session:
            count = await session.scalar(select(func.count(Payment.id))) or 0
        if callback.message:
            await callback.message.edit_text(
                f"Всего платежей: {count}", reply_markup=admin_menu()
            )
    elif action == "promos":
        async with session_factory() as session:
            count = await session.scalar(select(func.count(PromoCode.id))) or 0
        if callback.message:
            await callback.message.edit_text(
                f"Промокоды: {count}", reply_markup=promo_admin_menu()
            )
    elif action == "users":
        async with session_factory() as session:
            count = await session.scalar(select(func.count(User.id))) or 0
        if callback.message:
            await callback.message.edit_text(
                f"Пользователи: {count}", reply_markup=admin_menu()
            )
    elif action == "remnawave":
        async with session_factory() as session:
            linked = (
                await session.scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.remnawave_user_uuid.is_not(None)
                    )
                )
                or 0
            )
            active = (
                await session.scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.provisioning_status == ProvisioningStatus.active
                    )
                )
                or 0
            )
            pending = (
                await session.scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.provisioning_status.in_(
                            [
                                ProvisioningStatus.pending,
                                ProvisioningStatus.provisioning,
                            ]
                        )
                    )
                )
                or 0
            )
            failed = (
                await session.scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.provisioning_status == ProvisioningStatus.failed
                    )
                )
                or 0
            )
            last_sync = await session.scalar(
                select(func.max(Subscription.remnawave_last_sync_at))
            )
        if callback.message:
            await callback.message.edit_text(
                "🌐 Remnawave\n\n"
                f"Связано: {linked}\nАктивно: {active}\n"
                f"Pending: {pending}\nFailed: {failed}\n"
                f"Последняя синхронизация: {last_sync or '—'}\n\n"
                "Команды: /sync_remnawave, /grant_vpn, /rw_user, "
                "/rw_disable, /rw_enable",
                reply_markup=remnawave_admin_menu(),
            )
    elif action == "broadcast":
        if callback.message:
            await callback.message.edit_text(
                "Рассылка будет реализована отдельным безопасным сценарием.",
                reply_markup=admin_menu(),
            )
    elif action == "settings":
        if callback.message:
            await callback.message.edit_text("Настройки", reply_markup=admin_menu())
    elif action == "stats_v3":
        async with session_factory() as session:
            users = await session.scalar(select(func.count(User.id))) or 0
            active_tariffs = (
                await session.scalar(
                    select(func.count(Tariff.id)).where(Tariff.is_active.is_(True))
                )
                or 0
            )
            total_orders = await session.scalar(select(func.count(Order.id))) or 0
            order_counts = dict(
                (
                    await session.execute(
                        select(Order.status, func.count(Order.id)).group_by(
                            Order.status
                        )
                    )
                ).all()
            )
            payment_counts = dict(
                (
                    await session.execute(
                        select(Payment.status, func.count(Payment.id)).group_by(
                            Payment.status
                        )
                    )
                ).all()
            )
            income = await session.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0))
                .join(Order, Order.id == Payment.order_id)
                .where(
                    Payment.status == PaymentStatus.paid,
                    Order.status == OrderStatus.completed,
                )
            )
            active_subscriptions = (
                await session.scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.status == SubscriptionStatus.active
                    )
                )
                or 0
            )
            trial_subscriptions = (
                await session.scalar(
                    select(func.count(Subscription.id)).where(
                        Subscription.source_type == SubscriptionSource.trial,
                        Subscription.status.in_(
                            [SubscriptionStatus.active, SubscriptionStatus.pending]
                        ),
                        Subscription.expires_at > func.now(),
                    )
                )
                or 0
            )
            trial_used = (
                await session.scalar(
                    select(func.count(User.id)).where(User.trial_used.is_(True))
                )
                or 0
            )
            active_promos = (
                await session.scalar(
                    select(func.count(PromoCode.id)).where(
                        PromoCode.is_active.is_(True),
                        or_(
                            PromoCode.valid_from.is_(None),
                            PromoCode.valid_from <= func.now(),
                        ),
                        or_(
                            PromoCode.valid_until.is_(None),
                            PromoCode.valid_until > func.now(),
                        ),
                    )
                )
                or 0
            )
            promo_usages = (
                await session.scalar(select(func.count(PromoCodeUsage.id))) or 0
            )
        total_payments = sum(payment_counts.values())
        paid_payments = payment_counts.get(PaymentStatus.paid, 0)
        unpaid_payments = payment_counts.get(
            PaymentStatus.created, 0
        ) + payment_counts.get(PaymentStatus.pending, 0)
        cancelled_payments = payment_counts.get(PaymentStatus.cancelled, 0)
        text = (
            f"Пользователей: {users}\n"
            f"Активных тарифов: {active_tariffs}\n"
            f"Заказов: {total_orders}\n"
            f"Ожидают оплаты: {order_counts.get(OrderStatus.awaiting_payment, 0)}\n"
            f"Завершено: {order_counts.get(OrderStatus.completed, 0)}\n"
            f"Отменено: {order_counts.get(OrderStatus.cancelled, 0)}\n\n"
            f"Всего платежей: {total_payments}\n"
            f"Успешных платежей: {paid_payments}\n"
            f"Неоплаченных платежей: {unpaid_payments}\n"
            f"Отменённых платежей: {cancelled_payments}\n"
            f"Подтверждённый доход: {Decimal(income):.2f} ₽\n\n"
            f"Активных подписок: {active_subscriptions}\n"
            f"Пробных подписок: {trial_subscriptions}\n"
            f"Использовано trial: {trial_used}\n"
            f"Активных промокодов: {active_promos}\n"
            f"Применений промокодов: {promo_usages}"
        )
        if callback.message:
            await callback.message.edit_text(text, reply_markup=admin_menu())
    elif action == "stats":
        async with session_factory() as session:
            users = await session.scalar(select(func.count(User.id))) or 0
            active = (
                await session.scalar(
                    select(func.count(Tariff.id)).where(Tariff.is_active.is_(True))
                )
                or 0
            )
            total = await session.scalar(select(func.count(Order.id))) or 0
            counts = dict(
                (
                    await session.execute(
                        select(Order.status, func.count(Order.id)).group_by(
                            Order.status
                        )
                    )
                ).all()
            )
        text = (
            f"Всего пользователей: {users}\n"
            f"Активных тарифов: {active}\nВсего заказов: {total}\n"
            f"Pending: {counts.get(OrderStatus.pending, 0)}\n"
            f"Awaiting payment: {counts.get(OrderStatus.awaiting_payment, 0)}\n"
            f"Completed: {counts.get(OrderStatus.completed, 0)}\n"
            f"Cancelled: {counts.get(OrderStatus.cancelled, 0)}"
        )
        if callback.message:
            await callback.message.edit_text(text, reply_markup=admin_menu())
    await callback.answer()


@router.callback_query(AdminCallback.filter())
async def reject_admin_callback(callback: CallbackQuery) -> None:
    await callback.answer("⛔ Нет доступа", show_alert=True)


async def invalid(message: Message, text: str) -> None:
    await message.answer(f"Некорректное значение. {text}")


@router.message(TariffForm.name)
async def form_name(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.strip():
        await invalid(message, "Введите название.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(TariffForm.description)
    await message.answer("Введите описание или '-' без описания:")


@router.message(TariffForm.description)
async def form_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=None if message.text == "-" else message.text)
    await state.set_state(TariffForm.duration)
    await message.answer("Срок в днях (целое число больше 0):")


async def positive_int(
    message: Message, state: FSMContext, key: str, next_state: State, prompt: str
) -> None:
    try:
        value = int(message.text or "")
        if value <= 0:
            raise ValueError
    except ValueError:
        await invalid(message, "Нужно целое число больше 0.")
        return
    await state.update_data(**{key: value})
    await state.set_state(next_state)
    await message.answer(prompt)


@router.message(TariffForm.duration)
async def form_duration(message: Message, state: FSMContext) -> None:
    await positive_int(
        message, state, "duration_days", TariffForm.price, "Цена (например 199.00):"
    )


@router.message(TariffForm.price)
async def form_price(message: Message, state: FSMContext) -> None:
    try:
        value = Decimal((message.text or "").replace(",", "."))
        if value <= 0 or value.as_tuple().exponent < -2:
            raise InvalidOperation
    except InvalidOperation:
        await invalid(
            message, "Введите положительную сумму, не более 2 знаков после точки."
        )
        return
    await state.update_data(price=str(value))
    await state.set_state(TariffForm.currency)
    await message.answer("Валюта (3 буквы, например RUB):")


@router.message(TariffForm.currency)
async def form_currency(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().upper()
    if len(value) != 3 or not value.isalpha():
        await invalid(message, "Введите трёхбуквенный код валюты.")
        return
    await state.update_data(currency=value)
    await state.set_state(TariffForm.traffic)
    await message.answer("Лимит трафика в ГБ (для безлимита временно укажите 1):")


@router.message(TariffForm.traffic)
async def form_traffic(message: Message, state: FSMContext) -> None:
    await positive_int(
        message,
        state,
        "traffic_limit_gb",
        TariffForm.unlimited,
        "Безлимитный трафик? да/нет:",
    )


def yes_no(text: str | None) -> bool | None:
    value = (text or "").strip().lower()
    if value in {"да", "yes", "y"}:
        return True
    if value in {"нет", "no", "n"}:
        return False
    return None


@router.message(TariffForm.unlimited)
async def form_unlimited(message: Message, state: FSMContext) -> None:
    value = yes_no(message.text)
    if value is None:
        await invalid(message, "Ответьте да или нет.")
        return
    await state.update_data(
        is_unlimited_traffic=value,
        traffic_limit_gb=None
        if value
        else (await state.get_data())["traffic_limit_gb"],
    )
    await state.set_state(TariffForm.devices)
    await message.answer("Количество устройств:")


@router.message(TariffForm.devices)
async def form_devices(message: Message, state: FSMContext) -> None:
    await positive_int(
        message,
        state,
        "device_limit",
        TariffForm.sort_order,
        "Порядок отображения (целое число):",
    )


@router.message(TariffForm.sort_order)
async def form_sort(message: Message, state: FSMContext) -> None:
    try:
        value = int(message.text or "")
    except ValueError:
        await invalid(message, "Введите целое число.")
        return
    await state.update_data(sort_order=value)
    await state.set_state(TariffForm.active)
    await message.answer("Тариф активен? да/нет:")


@router.message(TariffForm.active)
async def form_active(message: Message, state: FSMContext) -> None:
    value = yes_no(message.text)
    if value is None:
        await invalid(message, "Ответьте да или нет.")
        return
    await state.update_data(is_active=value)
    data = await state.get_data()
    await state.set_state(TariffForm.confirm)
    await message.answer(
        f"Подтвердите сохранение:\n{data['name']}, {data['duration_days']} дней, "
        f"{data['price']} {data['currency']}",
        reply_markup=confirm_form(),
    )


from app.bot.handlers.admin_promos import router as admin_promos_router  # noqa: E402

router.include_router(admin_promos_router)
