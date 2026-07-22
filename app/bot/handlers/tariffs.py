import logging
import uuid

from aiogram import F, Router
from aiogram.types import CallbackQuery
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import OrderCallback, TariffCallback
from app.bot.keyboards import build_main_menu, build_payment
from app.bot.keyboards.tariffs import (
    build_order,
    build_tariff_card,
    build_tariffs,
    money,
)
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import Payment
from app.database.repositories import (
    OrderOwnershipError,
    OrderRepository,
    TariffRepository,
    UserRepository,
)
from app.integrations.onlipay.client import OnliPayClient
from app.integrations.onlipay.exceptions import OnliPayError
from app.integrations.remnawave.client import RemnawaveClient
from app.services.payments import PaymentService, PaymentValidationError
from app.services.remnawave_factory import build_subscription_service

logger = logging.getLogger(__name__)
router = Router(name=__name__)


def traffic(limit: int | None, unlimited: bool) -> str:
    return "Безлимит" if unlimited else f"{limit} ГБ"


@router.callback_query(F.data.in_({"tariffs", "buy_vpn"}))
async def show_tariffs(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        tariffs = await TariffRepository(session).get_active()
    await callback.answer()
    if callback.message:
        text = "Выберите тариф:" if tariffs else "Активных тарифов пока нет."
        await callback.message.edit_text(text, reply_markup=build_tariffs(tariffs))


@router.callback_query(TariffCallback.filter(F.action == "view"))
async def show_tariff(
    callback: CallbackQuery,
    callback_data: TariffCallback,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        tariff = await TariffRepository(session).get_by_id(callback_data.tariff_id)
    if tariff is None or not tariff.is_active:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    description = f"\n{tariff.description}\n" if tariff.description else "\n"
    text = (
        f"🚀 Тариф «{tariff.name}»\n{description}\n"
        f"Срок: {tariff.duration_days} дней\n"
        f"Трафик: {traffic(tariff.traffic_limit_gb, tariff.is_unlimited_traffic)}\n"
        f"Устройства: до {tariff.device_limit}\n"
        f"Стоимость: {money(tariff.price, tariff.currency)}"
    )
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            text, reply_markup=build_tariff_card(tariff.id)
        )


@router.callback_query(TariffCallback.filter(F.action == "buy"))
async def buy_tariff(
    callback: CallbackQuery,
    callback_data: TariffCallback,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session, session.begin():
        user = await UserRepository(session).get_by_telegram_id(callback.from_user.id)
        tariff = await TariffRepository(session).get_by_id(callback_data.tariff_id)
        if user is None:
            await callback.answer("Сначала нажмите /start", show_alert=True)
            return
        if tariff is None or not tariff.is_active:
            await callback.answer("Тариф недоступен", show_alert=True)
            return
        order = await OrderRepository(session).create_from_tariff(user.id, tariff)
    order_traffic = traffic(
        order.traffic_limit_gb_snapshot, order.is_unlimited_traffic_snapshot
    )
    text = (
        "🧾 Заказ создан\n\n"
        f"Тариф: {order.tariff_name_snapshot}\n"
        f"Срок: {order.duration_days_snapshot} дней\n"
        f"Трафик: {order_traffic}\n"
        f"Устройства: до {order.device_limit_snapshot}\n"
        f"К оплате: {money(order.final_amount, order.currency_snapshot)}\n\n"
        f"Номер заказа: {str(order.id)[:8]}"
    )
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(text, reply_markup=build_order(order))


@router.callback_query(OrderCallback.filter(F.action == "pay"))
async def create_payment(
    callback: CallbackQuery,
    callback_data: OrderCallback,
    session_factory: async_sessionmaker[AsyncSession],
    onlipay_client: OnliPayClient,
    public_base_url: str | None,
) -> None:
    try:
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                callback.from_user.id
            )
            if user is None:
                raise PaymentValidationError("order_not_found")
            payment = await PaymentService(
                session, onlipay_client, public_base_url=public_base_url
            ).create_for_order(callback_data.order_id, user_id=user.id)
    except OnliPayError:
        await callback.answer(
            "Оплата OnliPay пока недоступна: требуется официальный merchant-контракт.",
            show_alert=True,
        )
        return
    except PaymentValidationError:
        await callback.answer("Заказ нельзя оплатить", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "Платёж создан. Сумма и заказ проверены сервером.",
            reply_markup=build_payment(payment),
        )


@router.callback_query(OrderCallback.filter(F.action == "check"))
async def check_payment(
    callback: CallbackQuery,
    callback_data: OrderCallback,
    session_factory: async_sessionmaker[AsyncSession],
    onlipay_client: OnliPayClient,
    public_base_url: str | None,
    redis_client: Redis,
    admin_ids: set[int],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
) -> None:
    cooldown_key = f"payment-check:{callback.from_user.id}:{callback_data.order_id}"
    if not await redis_client.set(cooldown_key, "1", ex=12, nx=True):
        await callback.answer(
            "Повторите проверку через несколько секунд.", show_alert=True
        )
        return
    try:
        order_uuid = uuid.UUID(callback_data.order_id)
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                callback.from_user.id
            )
            order = await OrderRepository(session).get_by_id(order_uuid)
            if user is None or order is None or order.user_id != user.id:
                raise PaymentValidationError("order_not_found")
            payment = await session.scalar(
                select(Payment)
                .where(Payment.order_id == order.id)
                .order_by(Payment.created_at.desc())
                .limit(1)
            )
            if payment is None:
                raise PaymentValidationError("payment_not_found")
            result = await PaymentService(
                session,
                onlipay_client,
                public_base_url=public_base_url,
                subscription_service=build_subscription_service(
                    session,
                    remnawave_client,
                    subscription_cipher,
                    remnawave_internal_squad_uuid,
                ),
            ).check_status(payment, user_id=user.id)
    except (ValueError, PaymentValidationError):
        await callback.answer("Платёж не найден", show_alert=True)
        return
    except OnliPayError:
        await callback.answer(
            "Проверка OnliPay недоступна до подключения merchant API.",
            show_alert=True,
        )
        return
    if result is None:
        await callback.answer("Оплата пока не найдена", show_alert=True)
    elif result.completed:
        await callback.answer("Оплата подтверждена", show_alert=True)
        if callback.message:
            text = "✅ Оплата подтверждена. Подписка активирована."
            if (
                result.subscription is not None
                and result.subscription.subscription_url_encrypted
                and subscription_cipher is not None
            ):
                url = subscription_cipher.decrypt(
                    result.subscription.subscription_url_encrypted
                )
                text += f"\n\nВаша индивидуальная ссылка:\n\n{url}"
            await callback.message.answer(text)
    else:
        await callback.answer(
            "Оплата подтверждена, активация будет повторена автоматически.",
            show_alert=True,
        )
        for admin_id in admin_ids:
            await callback.bot.send_message(
                admin_id,
                "⚠️ Ошибка активации оплаченного заказа. "
                f"Заказ: {str(result.order.id)[:8]}",
            )


@router.callback_query(OrderCallback.filter(F.action == "cancel"))
async def cancel_order(
    callback: CallbackQuery,
    callback_data: OrderCallback,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    try:
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                callback.from_user.id
            )
            if user is None:
                raise LookupError
            order = await OrderRepository(session).cancel(
                callback_data.order_id, user.id
            )
    except (LookupError, OrderOwnershipError):
        await callback.answer("Заказ не найден", show_alert=True)
        return
    await callback.answer(
        "Заказ отменён"
        if order.status.value == "cancelled"
        else "Заказ уже нельзя отменить",
        show_alert=True,
    )
    if callback.message and order.status.value == "cancelled":
        await callback.message.edit_reply_markup(reply_markup=None)


@router.callback_query(F.data == "main_menu")
async def show_main_menu(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message:
        await callback.message.edit_text(
            "Выберите нужный раздел:", reply_markup=build_main_menu()
        )
