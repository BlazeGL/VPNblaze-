import logging
from decimal import Decimal, InvalidOperation

from aiogram import Router
from aiogram.enums import ContentType
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import CallbackQuery, Message
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import AdminCallback
from app.bot.filters import AdminFilter
from app.bot.handlers.admin_promos import render_promo_list
from app.bot.keyboards.admin import (
    admin_menu,
    admin_navigation,
    admin_price_navigation,
    admin_sales_menu,
    admin_tariff_actions,
    admin_tariff_management,
    admin_tariffs,
    admin_users_menu,
    broadcast_confirm_keyboard,
    confirm_form,
    remnawave_admin_menu,
)
from app.bot.keyboards.tariffs import money
from app.database.models import (
    Order,
    OrderStatus,
    Payment,
    PaymentStatus,
    PromoCode,
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    Tariff,
    User,
)
from app.database.repositories import TariffRepository, UserRepository
from app.services.audit import add_audit_log
from app.services.broadcasts import copy_broadcast_to_users
from app.services.referrals import ReferralService, ReferralStats
from app.services.trials import TrialService

router = Router(name=__name__)
logger = logging.getLogger(__name__)
MAX_TARIFF_PRICE = Decimal("99999999.99")
ACTIVE_BROADCAST_ADMINS: set[int] = set()


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


class TariffPriceForm(StatesGroup):
    price = State()


class TariffButtonTextForm(StatesGroup):
    text = State()


class UserSearchForm(StatesGroup):
    telegram_id = State()


class TrialAccessForm(StatesGroup):
    command = State()


class BroadcastForm(StatesGroup):
    content = State()
    confirm = State()
    sending = State()


BROADCAST_CONTENT_TYPES = {
    ContentType.TEXT,
    ContentType.PHOTO,
    ContentType.VIDEO,
    ContentType.ANIMATION,
    ContentType.DOCUMENT,
    ContentType.AUDIO,
    ContentType.VOICE,
    ContentType.VIDEO_NOTE,
    ContentType.STICKER,
}


async def admin_dashboard_text(
    session_factory: async_sessionmaker[AsyncSession],
) -> str:
    async with session_factory() as session:
        users = await session.scalar(select(func.count(User.id))) or 0
        active_subscriptions = (
            await session.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.status == SubscriptionStatus.active
                )
            )
            or 0
        )
        awaiting_payment = (
            await session.scalar(
                select(func.count(Order.id)).where(
                    Order.status == OrderStatus.awaiting_payment
                )
            )
            or 0
        )
        income = await session.scalar(
            select(func.coalesce(func.sum(Payment.amount), 0))
            .join(Order, Order.id == Payment.order_id)
            .where(
                Payment.status == PaymentStatus.paid,
                Order.status == OrderStatus.completed,
            )
        )
        failed_activations = (
            await session.scalar(
                select(func.count(Subscription.id)).where(
                    Subscription.provisioning_status
                    == ProvisioningStatus.failed
                )
            )
            or 0
        )
    return (
        "⚙️ Панель управления\n\n"
        f"👥 Пользователей: {users} · активных подписок: "
        f"{active_subscriptions}\n"
        f"💳 Ожидают оплаты: {awaiting_payment} · доход: "
        f"{Decimal(income):.2f} ₽\n"
        f"⚠️ Ошибок активации: {failed_activations}"
    )


def parse_tariff_price(text: str | None) -> Decimal:
    try:
        value = Decimal((text or "").strip().replace(",", "."))
    except (InvalidOperation, ValueError) as exc:
        raise ValueError("invalid_price") from exc
    if (
        not value.is_finite()
        or value <= 0
        or value > MAX_TARIFF_PRICE
        or value.as_tuple().exponent < -2
    ):
        raise ValueError("invalid_price")
    return value.quantize(Decimal("0.01"))


def parse_tariff_button_text(text: str | None) -> str:
    value = (text or "").strip()
    if not value or len(value) > 255 or "\n" in value or "\r" in value:
        raise ValueError("invalid_button_text")
    return value


def tariff_shows_button_price(item: Tariff) -> bool:
    return getattr(item, "show_price_in_button", None) is not False


def tariff_price_prompt(item: Tariff) -> str:
    price_visibility = (
        "показывается рядом с текстом кнопки"
        if tariff_shows_button_price(item)
        else "скрыта; клиент увидит только заданный текст кнопки"
    )
    return (
        "💰 Изменение цены\n\n"
        f"{item.name}\n"
        f"Фактическая цена оплаты: {money(item.price, item.currency)}\n"
        f"Отображение полной цены: {price_visibility}.\n\n"
        "Отправьте новую фактическую цену числом, например 299 или 299,50.\n\n"
        "Текст кнопки и показ полной цены можно изменить кнопками ниже."
    )


def tariff_card_text(item: Tariff) -> str:
    traffic = (
        "без ограничений"
        if item.is_unlimited_traffic
        else f"{item.traffic_limit_gb} ГБ"
    )
    return (
        "💳 Управление тарифом\n\n"
        f"{item.name}\n"
        f"Цена: {money(item.price, item.currency)}\n"
        "Полная цена в кнопке: "
        f"{'показывается' if tariff_shows_button_price(item) else 'скрыта'}\n"
        f"Срок: {item.duration_days} дней\n"
        f"Трафик: {traffic}\n"
        f"Устройств: {item.device_limit}\n"
        f"Показывается клиентам: {'да' if item.is_active else 'нет'}"
    )


def subscription_status_text(subscription: Subscription | None) -> str:
    if subscription is None:
        return "нет подписки"
    return {
        SubscriptionStatus.pending: "ожидает активации",
        SubscriptionStatus.active: "активна",
        SubscriptionStatus.expired: "закончилась",
        SubscriptionStatus.disabled: "отключена",
        SubscriptionStatus.activation_failed: "ошибка активации",
    }[subscription.status]


@router.message(Command("edik"), AdminFilter())
async def open_edik(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await state.clear()
    await message.answer(
        await admin_dashboard_text(session_factory),
        reply_markup=admin_menu(),
    )


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
async def open_admin(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    await state.clear()
    await message.answer(
        await admin_dashboard_text(session_factory),
        reply_markup=admin_menu(),
    )


@router.message(Command("ref_stats"), AdminFilter())
async def referral_stats(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        stats = await ReferralService(session).global_stats()
    await message.answer(referral_stats_text(stats))


def referral_stats_text(stats: ReferralStats) -> str:
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
    return (
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
                await admin_dashboard_text(session_factory),
                reply_markup=admin_menu(),
            )
    elif action == "tariffs":
        await state.clear()
        async with session_factory() as session:
            items = await TariffRepository(session).get_all()
        if callback.message:
            await callback.message.edit_text(
                "💳 Тарифы и цены\n\n"
                + (
                    "Текущая цена указана на каждой кнопке. "
                    "Нажмите на тариф, чтобы изменить её."
                    if items
                    else "Тарифов пока нет. Добавьте первый тариф "
                    "через раздел управления."
                ),
                reply_markup=admin_tariffs(items),
            )
    elif action == "tariff_management":
        await state.clear()
        async with session_factory() as session:
            items = await TariffRepository(session).get_all()
        if callback.message:
            await callback.message.edit_text(
                "⚙️ Управление тарифами\n\n"
                "Здесь можно изменить параметры тарифа, скрыть его "
                "или добавить новый.",
                reply_markup=admin_tariff_management(items),
            )
    elif action == "price":
        await state.clear()
        async with session_factory() as session:
            item = await TariffRepository(session).get_by_id(
                callback_data.tariff_id
            )
        if item is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return
        await state.update_data(tariff_id=item.id)
        await state.set_state(TariffPriceForm.price)
        if callback.message:
            await callback.message.edit_text(
                tariff_price_prompt(item),
                reply_markup=admin_price_navigation(item),
            )
    elif action == "button_text":
        await state.clear()
        async with session_factory() as session:
            item = await TariffRepository(session).get_by_id(
                callback_data.tariff_id
            )
        if item is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return
        await state.update_data(tariff_id=item.id)
        await state.set_state(TariffButtonTextForm.text)
        if callback.message:
            await callback.message.edit_text(
                "✏️ Текст кнопки тарифа\n\n"
                f"Сейчас:\n{item.name}\n\n"
                "Отправьте новый текст одной строкой. Например:\n"
                "Пополнить на 3 месяца — 180 ₽/мес",
                reply_markup=admin_navigation("tariffs"),
            )
    elif action == "toggle_button_price":
        await state.clear()
        async with session_factory() as session, session.begin():
            repository = TariffRepository(session)
            item = await repository.get_by_id(callback_data.tariff_id)
            if item is not None:
                item = await repository.update(
                    item,
                    show_price_in_button=not tariff_shows_button_price(item),
                )
                actor = await UserRepository(session).get_by_telegram_id(
                    callback.from_user.id
                )
                add_audit_log(
                    session,
                    action="admin_tariff_button_price_visibility_changed",
                    entity_type="tariff",
                    entity_id=item.id,
                    actor_user_id=actor.id if actor else None,
                    actor_telegram_id=callback.from_user.id,
                    details={
                        "show_price_in_button": item.show_price_in_button,
                    },
                )
        if item is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return
        await state.update_data(tariff_id=item.id)
        await state.set_state(TariffPriceForm.price)
        if callback.message:
            await callback.message.edit_text(
                tariff_price_prompt(item),
                reply_markup=admin_price_navigation(item),
            )
    elif action == "edit":
        await state.clear()
        async with session_factory() as session:
            item = await TariffRepository(session).get_by_id(callback_data.tariff_id)
        if item is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return
        if callback.message:
            await callback.message.edit_text(
                tariff_card_text(item),
                reply_markup=admin_tariff_actions(item),
            )
    elif action == "toggle":
        await state.clear()
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
        if item is None:
            await callback.answer("Тариф не найден", show_alert=True)
            return
        if callback.message:
            await callback.message.edit_text(
                tariff_card_text(item),
                reply_markup=admin_tariff_actions(item),
            )
    elif action in {"create", "form"}:
        await state.clear()
        current = None
        if action == "form":
            async with session_factory() as session:
                current = await TariffRepository(session).get_by_id(
                    callback_data.tariff_id
                )
            if current is None:
                await callback.answer("Тариф не найден", show_alert=True)
                return
        await state.update_data(tariff_id=callback_data.tariff_id or None)
        await state.set_state(TariffForm.name)
        if callback.message:
            prompt = "Введите название тарифа:"
            if current is not None:
                prompt = (
                    "✏️ Изменение параметров тарифа\n\n"
                    f"Текущее название: {current.name}\n"
                    "Введите название заново:"
                )
            await callback.message.edit_text(
                prompt,
                reply_markup=admin_navigation("tariff_management"),
            )
    elif action == "save":
        data = await state.get_data()
        required = {
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
        }
        if not required.issubset(data):
            await state.clear()
            await callback.answer(
                "Форма устарела. Откройте тариф заново.",
                show_alert=True,
            )
            return
        values = {
            key: data[key]
            for key in required
        }
        # FSM storage keeps JSON-compatible strings; convert the monetary value
        # back to Decimal before assigning it to the Numeric ORM field.
        values["price"] = parse_tariff_price(str(values["price"]))
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
                "✅ Тариф сохранён.\n\n" + tariff_card_text(saved),
                reply_markup=admin_tariff_actions(saved),
            )
    elif action in {"sales", "sales_summary"}:
        await state.clear()
        async with session_factory() as session:
            total_orders = await session.scalar(select(func.count(Order.id))) or 0
            awaiting_payment = (
                await session.scalar(
                    select(func.count(Order.id)).where(
                        Order.status == OrderStatus.awaiting_payment
                    )
                )
                or 0
            )
            completed_orders = (
                await session.scalar(
                    select(func.count(Order.id)).where(
                        Order.status == OrderStatus.completed
                    )
                )
                or 0
            )
            paid_payments = (
                await session.scalar(
                    select(func.count(Payment.id)).where(
                        Payment.status == PaymentStatus.paid
                    )
                )
                or 0
            )
            income = await session.scalar(
                select(func.coalesce(func.sum(Payment.amount), 0))
                .join(Order, Order.id == Payment.order_id)
                .where(
                    Payment.status == PaymentStatus.paid,
                    Order.status == OrderStatus.completed,
                )
            )
            active_promos = (
                await session.scalar(
                    select(func.count(PromoCode.id)).where(
                        PromoCode.is_active.is_(True),
                        (
                            PromoCode.valid_from.is_(None)
                            | (PromoCode.valid_from <= func.now())
                        ),
                        (
                            PromoCode.valid_until.is_(None)
                            | (PromoCode.valid_until > func.now())
                        ),
                        (
                            PromoCode.max_uses.is_(None)
                            | (PromoCode.uses_count < PromoCode.max_uses)
                        ),
                    )
                )
                or 0
            )
        if callback.message:
            await callback.message.edit_text(
                "🧾 Продажи\n\n"
                f"Заказов всего: {total_orders}\n"
                f"Ожидают оплаты: {awaiting_payment}\n"
                f"Завершено: {completed_orders}\n"
                f"Успешных платежей: {paid_payments}\n"
                f"Подтверждённый доход: {Decimal(income):.2f} ₽\n"
                f"Активных промокодов: {active_promos}",
                reply_markup=admin_sales_menu(),
            )
    elif action == "orders":
        async with session_factory() as session:
            count = await session.scalar(select(func.count(Order.id)))
        if callback.message:
            await callback.message.edit_text(
                f"Заказов всего: {count or 0}",
                reply_markup=admin_navigation("sales"),
            )
    elif action == "payments":
        async with session_factory() as session:
            count = await session.scalar(select(func.count(Payment.id))) or 0
        if callback.message:
            await callback.message.edit_text(
                f"Платежей всего: {count}",
                reply_markup=admin_navigation("sales"),
            )
    elif action == "promos":
        await state.clear()
        if callback.message:
            await render_promo_list(callback.message, session_factory, page=0)
    elif action in {"users", "users_section"}:
        await state.clear()
        async with session_factory() as session:
            count = await session.scalar(select(func.count(User.id))) or 0
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
                            [
                                SubscriptionStatus.active,
                                SubscriptionStatus.pending,
                            ]
                        ),
                        Subscription.expires_at > func.now(),
                    )
                )
                or 0
            )
        if callback.message:
            await callback.message.edit_text(
                "👥 Пользователи\n\n"
                f"Всего: {count}\n"
                f"С активной подпиской: {active_subscriptions}\n"
                f"На пробном периоде: {trial_subscriptions}",
                reply_markup=admin_users_menu(),
            )
    elif action == "user_search":
        await state.clear()
        await state.set_state(UserSearchForm.telegram_id)
        if callback.message:
            await callback.message.edit_text(
                "🔎 Поиск пользователя\n\n"
                "Отправьте Telegram ID пользователя.",
                reply_markup=admin_navigation("users_section"),
            )
    elif action == "ref_stats":
        await state.clear()
        async with session_factory() as session:
            stats = await ReferralService(session).global_stats()
        if callback.message:
            await callback.message.edit_text(
                referral_stats_text(stats),
                reply_markup=admin_navigation("users_section"),
            )
    elif action == "trial_access":
        await state.clear()
        await state.set_state(TrialAccessForm.command)
        if callback.message:
            await callback.message.edit_text(
                "🧪 Пробный доступ\n\n"
                "Отправьте Telegram ID и действие через пробел:\n"
                "• 123456 включить\n"
                "• 123456 отключить",
                reply_markup=admin_navigation("users_section"),
            )
    elif action == "remnawave":
        await state.clear()
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
                "🌐 VPN-доступ\n\n"
                f"Подключено пользователей: {linked}\n"
                f"Работают: {active}\n"
                f"Ожидают активации: {pending}\n"
                f"Требуют внимания: {failed}\n"
                f"Последнее обновление: {last_sync or 'ещё не выполнялось'}",
                reply_markup=remnawave_admin_menu(),
            )
    elif action == "broadcast":
        await state.clear()
        await state.set_state(BroadcastForm.content)
        if callback.message:
            await callback.message.edit_text(
                "📣 Рассылка\n\n"
                "Отправьте одно сообщение для рассылки. Можно использовать "
                "текст, фото, видео или файл.",
                reply_markup=admin_navigation(),
            )
    elif action == "broadcast_send":
        admin_id = callback.from_user.id
        if admin_id in ACTIVE_BROADCAST_ADMINS:
            await callback.answer(
                "Эта рассылка уже выполняется.",
                show_alert=True,
            )
            return
        ACTIVE_BROADCAST_ADMINS.add(admin_id)
        try:
            data = await state.get_data()
            if (
                await state.get_state() != BroadcastForm.confirm.state
                or not isinstance(data.get("source_chat_id"), int)
                or not isinstance(data.get("source_message_id"), int)
            ):
                await callback.answer(
                    "Подтверждение устарело. Начните рассылку заново.",
                    show_alert=True,
                )
                return
            if callback.message is None:
                await callback.answer("Сообщение не найдено", show_alert=True)
                return
            await state.set_state(BroadcastForm.sending)
            await callback.answer("Рассылка запущена")
            await callback.message.edit_text("📣 Рассылка выполняется…")
            async with session_factory() as session:
                telegram_ids = list(
                    await session.scalars(
                        select(User.telegram_id).order_by(User.id)
                    )
                )
            result = await copy_broadcast_to_users(
                callback.bot,
                telegram_ids,
                from_chat_id=data["source_chat_id"],
                message_id=data["source_message_id"],
            )
            await callback.message.edit_text(
                "✅ Рассылка завершена\n\n"
                f"Успешно отправлено: {result.successful}\n"
                f"Ошибок: {result.errors}",
                reply_markup=admin_menu(),
            )
        except Exception:
            logger.exception("Broadcast failed before the batch could finish")
            if callback.message:
                await callback.message.edit_text(
                    "⚠️ Рассылка не завершена из-за общей ошибки.\n\n"
                    "Попробуйте запустить её ещё раз.",
                    reply_markup=admin_menu(),
                )
        finally:
            try:
                await state.clear()
            finally:
                ACTIVE_BROADCAST_ADMINS.discard(admin_id)
        return
    elif action == "settings":
        if callback.message:
            await callback.message.edit_text("Настройки", reply_markup=admin_menu())
    elif action in {"stats_v3", "stats"}:
        await state.clear()
        if callback.message:
            await callback.message.edit_text(
                await admin_dashboard_text(session_factory),
                reply_markup=admin_menu(),
            )
    else:
        await callback.answer(
            "Эта кнопка устарела. Откройте панель заново.",
            show_alert=True,
        )
        return
    await callback.answer()


@router.callback_query(AdminCallback.filter())
async def reject_admin_callback(callback: CallbackQuery) -> None:
    await callback.answer("⛔ Нет доступа", show_alert=True)


async def invalid(message: Message, text: str) -> None:
    await message.answer(
        f"Некорректное значение. {text}",
        reply_markup=admin_navigation("tariff_management"),
    )


@router.message(BroadcastForm.content, AdminFilter())
async def capture_broadcast_message(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    if message.content_type not in BROADCAST_CONTENT_TYPES:
        await message.answer(
            "Этот тип сообщения нельзя отправить в рассылке. "
            "Используйте текст, фото, видео, аудио или файл.",
            reply_markup=admin_navigation(),
        )
        return
    if message.media_group_id is not None:
        await message.answer(
            "Альбомы пока не поддерживаются. Отправьте одно фото или видео.",
            reply_markup=admin_navigation(),
        )
        return

    async with session_factory() as session:
        recipients = await session.scalar(select(func.count(User.id))) or 0

    await state.update_data(
        source_chat_id=message.chat.id,
        source_message_id=message.message_id,
    )
    await state.set_state(BroadcastForm.confirm)
    await message.answer(
        "📣 Сообщение готово к отправке\n\n"
        f"Получателей: {recipients}\n"
        "Проверьте сообщение выше и подтвердите рассылку.",
        reply_markup=broadcast_confirm_keyboard(),
    )


@router.message(TariffPriceForm.price, AdminFilter())
async def update_tariff_price(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        new_price = parse_tariff_price(message.text)
    except ValueError:
        await message.answer(
            "Введите цену от 0,01 до 99 999 999,99, "
            "не более двух знаков после запятой.",
            reply_markup=admin_price_navigation(),
        )
        return

    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    if not isinstance(tariff_id, int) or tariff_id <= 0:
        await state.clear()
        await message.answer(
            "Форма устарела. Выберите тариф заново.",
            reply_markup=admin_navigation("tariffs"),
        )
        return

    async with session_factory() as session, session.begin():
        repository = TariffRepository(session)
        item = await repository.get_by_id(tariff_id)
        if item is None:
            await state.clear()
            await message.answer(
                "Тариф больше не найден.",
                reply_markup=admin_navigation("tariffs"),
            )
            return
        old_price = Decimal(item.price)
        item = await repository.update(item, price=new_price)
        actor_telegram_id = message.from_user.id if message.from_user else None
        actor = (
            await UserRepository(session).get_by_telegram_id(actor_telegram_id)
            if actor_telegram_id is not None
            else None
        )
        add_audit_log(
            session,
            action="admin_tariff_price_changed",
            entity_type="tariff",
            entity_id=item.id,
            actor_user_id=actor.id if actor else None,
            actor_telegram_id=actor_telegram_id,
            details={
                "old_price": str(old_price),
                "new_price": str(new_price),
                "currency": item.currency,
            },
        )
        items = await repository.get_all()

    await state.clear()
    await message.answer(
        "✅ Цена изменена\n\n"
        f"{item.name}: {money(old_price, item.currency)} → "
        f"{money(item.price, item.currency)}\n\n"
        "Новая цена уже используется для новых заказов.",
        reply_markup=admin_tariffs(items),
    )


@router.message(TariffButtonTextForm.text, AdminFilter())
async def update_tariff_button_text(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        new_text = parse_tariff_button_text(message.text)
    except ValueError:
        await message.answer(
            "Отправьте текст длиной от 1 до 255 символов одной строкой.",
            reply_markup=admin_navigation("tariffs"),
        )
        return

    data = await state.get_data()
    tariff_id = data.get("tariff_id")
    if not isinstance(tariff_id, int) or tariff_id <= 0:
        await state.clear()
        await message.answer(
            "Форма устарела. Выберите тариф заново.",
            reply_markup=admin_navigation("tariffs"),
        )
        return

    async with session_factory() as session, session.begin():
        repository = TariffRepository(session)
        item = await repository.get_by_id(tariff_id)
        if item is None:
            await state.clear()
            await message.answer(
                "Тариф больше не найден.",
                reply_markup=admin_navigation("tariffs"),
            )
            return
        old_text = item.name
        item = await repository.update(item, name=new_text)
        actor_telegram_id = message.from_user.id if message.from_user else None
        actor = (
            await UserRepository(session).get_by_telegram_id(actor_telegram_id)
            if actor_telegram_id is not None
            else None
        )
        add_audit_log(
            session,
            action="admin_tariff_button_text_changed",
            entity_type="tariff",
            entity_id=item.id,
            actor_user_id=actor.id if actor else None,
            actor_telegram_id=actor_telegram_id,
            details={
                "old_text": old_text,
                "new_text": item.name,
            },
        )

    await state.clear()
    await message.answer(
        "✅ Текст кнопки изменён\n\n" + tariff_card_text(item),
        reply_markup=admin_tariff_actions(item),
    )


@router.message(UserSearchForm.telegram_id, AdminFilter())
async def search_user(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        telegram_id = int((message.text or "").strip())
        if telegram_id <= 0:
            raise ValueError
    except ValueError:
        await message.answer(
            "Введите положительный Telegram ID.",
            reply_markup=admin_navigation("users_section"),
        )
        return

    async with session_factory() as session:
        row = (
            await session.execute(
                select(User, Subscription)
                .outerjoin(Subscription, Subscription.user_id == User.id)
                .where(User.telegram_id == telegram_id)
            )
        ).one_or_none()
    if row is None:
        await message.answer(
            "Пользователь не найден.",
            reply_markup=admin_navigation("users_section"),
        )
        return

    user, subscription = row
    await state.clear()
    username = f"@{user.username}" if user.username else "не указан"
    subscription_status = subscription_status_text(subscription)
    expiration = (
        subscription.expires_at.strftime("%d.%m.%Y %H:%M")
        if subscription is not None
        else "—"
    )
    await message.answer(
        "👤 Пользователь\n\n"
        f"Telegram ID: {user.telegram_id}\n"
        f"Имя: {username}\n"
        f"Баланс: {Decimal(user.balance):.2f} ₽\n"
        f"Подписка: {subscription_status}\n"
        f"Действует до: {expiration}",
        reply_markup=admin_users_menu(),
    )


@router.message(TrialAccessForm.command, AdminFilter())
async def update_trial_access(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    parts = (message.text or "").lower().split()
    try:
        telegram_id = int(parts[0])
        if telegram_id <= 0 or len(parts) != 2:
            raise ValueError
    except (ValueError, IndexError):
        await message.answer(
            "Используйте формат: 123456 включить или 123456 отключить.",
            reply_markup=admin_navigation("users_section"),
        )
        return
    action = parts[1]
    if action not in {"включить", "отключить"}:
        await message.answer(
            "Укажите действие «включить» или «отключить».",
            reply_markup=admin_navigation("users_section"),
        )
        return
    disabled = action == "отключить"
    try:
        async with session_factory() as session, session.begin():
            actor = await UserRepository(session).get_by_telegram_id(
                message.from_user.id
            )
            await TrialService(session).set_disabled(
                telegram_id,
                disabled=disabled,
                actor_user_id=actor.id if actor else None,
                actor_telegram_id=message.from_user.id,
            )
    except LookupError:
        await message.answer(
            "Пользователь не найден.",
            reply_markup=admin_navigation("users_section"),
        )
        return
    await state.clear()
    await message.answer(
        (
            "Пробный доступ отключён для пользователя."
            if disabled
            else "Пробный доступ снова разрешён пользователю."
        ),
        reply_markup=admin_users_menu(),
    )


@router.message(TariffForm.name, AdminFilter())
async def form_name(message: Message, state: FSMContext) -> None:
    if not message.text or not message.text.strip():
        await invalid(message, "Введите название.")
        return
    await state.update_data(name=message.text.strip())
    await state.set_state(TariffForm.description)
    await message.answer(
        "Введите описание или '-' без описания:",
        reply_markup=admin_navigation("tariff_management"),
    )


@router.message(TariffForm.description, AdminFilter())
async def form_description(message: Message, state: FSMContext) -> None:
    await state.update_data(description=None if message.text == "-" else message.text)
    await state.set_state(TariffForm.duration)
    await message.answer(
        "Срок в днях (целое число больше 0):",
        reply_markup=admin_navigation("tariff_management"),
    )


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
    await message.answer(
        prompt,
        reply_markup=admin_navigation("tariff_management"),
    )


@router.message(TariffForm.duration, AdminFilter())
async def form_duration(message: Message, state: FSMContext) -> None:
    await positive_int(
        message, state, "duration_days", TariffForm.price, "Цена (например 100.00):"
    )


@router.message(TariffForm.price, AdminFilter())
async def form_price(message: Message, state: FSMContext) -> None:
    try:
        value = parse_tariff_price(message.text)
    except ValueError:
        await invalid(
            message,
            "Введите цену от 0,01 до 99 999 999,99, "
            "не более 2 знаков после запятой.",
        )
        return
    await state.update_data(price=str(value))
    await state.set_state(TariffForm.currency)
    await message.answer(
        "Валюта (3 буквы, например RUB):",
        reply_markup=admin_navigation("tariff_management"),
    )


@router.message(TariffForm.currency, AdminFilter())
async def form_currency(message: Message, state: FSMContext) -> None:
    value = (message.text or "").strip().upper()
    if len(value) != 3 or not value.isalpha():
        await invalid(message, "Введите трёхбуквенный код валюты.")
        return
    await state.update_data(currency=value)
    await state.set_state(TariffForm.traffic)
    await message.answer(
        "Лимит трафика в ГБ (для безлимита временно укажите 1):",
        reply_markup=admin_navigation("tariff_management"),
    )


@router.message(TariffForm.traffic, AdminFilter())
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


@router.message(TariffForm.unlimited, AdminFilter())
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
    await message.answer(
        "Количество устройств:",
        reply_markup=admin_navigation("tariff_management"),
    )


@router.message(TariffForm.devices, AdminFilter())
async def form_devices(message: Message, state: FSMContext) -> None:
    await positive_int(
        message,
        state,
        "device_limit",
        TariffForm.sort_order,
        "Порядок отображения (целое число):",
    )


@router.message(TariffForm.sort_order, AdminFilter())
async def form_sort(message: Message, state: FSMContext) -> None:
    try:
        value = int(message.text or "")
    except ValueError:
        await invalid(message, "Введите целое число.")
        return
    await state.update_data(sort_order=value)
    await state.set_state(TariffForm.active)
    await message.answer(
        "Тариф активен? да/нет:",
        reply_markup=admin_navigation("tariff_management"),
    )


@router.message(TariffForm.active, AdminFilter())
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
