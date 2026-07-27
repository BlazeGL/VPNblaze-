import logging
import uuid
from decimal import Decimal, InvalidOperation
from html import escape

from aiogram import F, Router
from aiogram.enums import ChatType, ParseMode
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.types import (
    CallbackQuery,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    Message,
)
from redis.asyncio import Redis
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.callbacks import OrderCallback, TariffCallback
from app.bot.keyboards import build_payment
from app.bot.keyboards.start import (
    BUY_SUBSCRIPTION_CALLBACK,
    TARIFFS_CALLBACK,
)
from app.bot.keyboards.tariffs import (
    build_insufficient_funds,
    build_order,
    build_tariff_card,
    money,
)
from app.bot.rendering import edit_text_or_caption
from app.core.crypto import SubscriptionUrlCipher
from app.database.models import OrderPurpose, Payment, Tariff
from app.database.repositories import (
    OrderOwnershipError,
    OrderRepository,
    TariffRepository,
    UserRepository,
)
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.yookassa.client import YooKassaClient
from app.integrations.yookassa.exceptions import YooKassaError
from app.services.activation_notifications import send_activation_notification
from app.services.billing import BillingService, BillingValidationError
from app.services.payments import PaymentService, PaymentValidationError
from app.services.remnawave_factory import build_subscription_service

logger = logging.getLogger(__name__)
router = Router(name=__name__)


class WalletTopUp(StatesGroup):
    amount = State()


def traffic(limit: int | None, unlimited: bool) -> str:
    return "Безлимит" if unlimited else f"{limit} ГБ"


def render_tariff_screen(
    tariff: Tariff,
    tariffs: list[Tariff] | None = None,
) -> tuple[str, InlineKeyboardMarkup]:
    text = (
        f"⚡ <b>{escape(tariff.name)}</b>\n\n"
        f"💳 Стоимость: <b>{money(tariff.price, tariff.currency)}</b>\n"
        f"📅 Срок: <b>{tariff.duration_days} дней</b>\n"
        "🌐 Трафик: "
        f"<b>{traffic(tariff.traffic_limit_gb, tariff.is_unlimited_traffic)}</b>"
    )
    return text, build_tariff_card(tariff, tariffs)


@router.callback_query(
    F.data.in_({TARIFFS_CALLBACK, BUY_SUBSCRIPTION_CALLBACK})
)
async def show_tariffs(
    callback: CallbackQuery, session_factory: async_sessionmaker[AsyncSession]
) -> None:
    async with session_factory() as session:
        tariffs = await TariffRepository(session).get_active()
    await callback.answer()
    if callback.message:
        if tariffs:
            text, markup = render_tariff_screen(tariffs[0], tariffs)
        else:
            text = (
                "Сейчас нет доступных тарифов. Попробуйте позже или "
                "обратитесь в поддержку."
            )
            markup = InlineKeyboardMarkup(
                inline_keyboard=[
                    [
                        InlineKeyboardButton(
                            text="⬅️ Назад",
                            callback_data="main_menu",
                        )
                    ]
                ]
            )
        await edit_text_or_caption(
            callback.message,
            text,
            markup,
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(TariffCallback.filter(F.action == "view"))
async def view_tariff(
    callback: CallbackQuery,
    callback_data: TariffCallback,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        tariffs = await TariffRepository(session).get_active()
    tariff = next(
        (item for item in tariffs if item.id == callback_data.tariff_id),
        None,
    )
    if tariff is None:
        await callback.answer("Тариф недоступен", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        text, markup = render_tariff_screen(tariff, tariffs)
        await edit_text_or_caption(
            callback.message,
            text,
            markup,
            parse_mode=ParseMode.HTML,
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
        f"Ваш баланс: {money(user.balance)}\n\n"
        "Подтвердите покупку с баланса или оплатите заказ через ЮKassa.\n\n"
        f"Номер заказа: {str(order.id)[:8]}"
    )
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(callback.message, text, build_order(order))


@router.callback_query(OrderCallback.filter(F.action == "balance"))
async def purchase_from_balance(
    callback: CallbackQuery,
    callback_data: OrderCallback,
    session_factory: async_sessionmaker[AsyncSession],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
    remnawave_russia_squad_uuid: str | None = None,
    remnawave_template_user_uuid: str | None = None,
) -> None:
    try:
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                callback.from_user.id
            )
            if user is None:
                raise BillingValidationError("user_not_found")
            result = await BillingService(
                session,
                build_subscription_service(
                    session,
                    remnawave_client,
                    subscription_cipher,
                    remnawave_internal_squad_uuid,
                    remnawave_russia_squad_uuid,
                    remnawave_template_user_uuid,
                ),
            ).purchase_order(
                callback_data.order_id,
                user_id=user.id,
            )
    except BillingValidationError:
        await callback.answer("Заказ нельзя оплатить с баланса.", show_alert=True)
        return

    if not result.purchased and result.shortfall > 0:
        await callback.answer("Недостаточно средств", show_alert=True)
        if callback.message:
            text = (
                "❌ <b>Недостаточно средств</b>\n\n"
                f"Стоимость тарифа:\n<b>{money(result.amount)}</b>\n\n"
                f"Ваш баланс:\n<b>{money(result.balance_before)}</b>\n\n"
                f"Не хватает:\n<b>{money(result.shortfall)}</b>"
            )
            await edit_text_or_caption(
                callback.message,
                text,
                build_insufficient_funds(result.order, result.shortfall),
                parse_mode=ParseMode.HTML,
            )
        return
    if not result.purchased:
        await callback.answer(
            "Оплата списана, активация будет повторена автоматически.",
            show_alert=True,
        )
        if callback.message:
            await callback.message.answer(
                "✅ Оплата сохранена\n\n"
                f"Списано: {money(result.amount)}\n"
                f"Баланс: {money(result.balance_after)}\n\n"
                "Активация будет повторена автоматически."
            )
        return

    await callback.answer(
        "Подписка уже оплачена"
        if result.already_processed
        else "Подписка оплачена",
        show_alert=True,
    )
    if callback.message:
        if not result.already_processed:
            await callback.message.answer(
                "✅ Оплата сохранена\n\n"
                f"Списано: {money(result.amount)}\n"
                f"Баланс: {money(result.balance_after)}"
            )
    if result.subscription is not None:
        await send_activation_notification(
            session_factory,
            bot=callback.bot,
            subscription_id=result.subscription.id,
            cipher=subscription_cipher,
        )


async def _create_wallet_payment(
    *,
    session: AsyncSession,
    user_id: int,
    amount: Decimal,
    payment_client: YooKassaClient,
    payment_return_url: str | None,
    public_base_url: str | None,
) -> Payment:
    order = await OrderRepository(session).create_wallet_topup(user_id, amount)
    return await PaymentService(
        session,
        payment_client,
        public_base_url=public_base_url,
        return_url=payment_return_url,
    ).create_for_order(order.id, user_id=user_id)


@router.callback_query(OrderCallback.filter(F.action == "topup_shortfall"))
async def topup_shortfall(
    callback: CallbackQuery,
    callback_data: OrderCallback,
    session_factory: async_sessionmaker[AsyncSession],
    payment_client: YooKassaClient,
    payment_return_url: str | None,
    public_base_url: str | None,
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
    remnawave_russia_squad_uuid: str | None = None,
    remnawave_template_user_uuid: str | None = None,
) -> None:
    try:
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                callback.from_user.id
            )
            order = await OrderRepository(session).get_by_id(callback_data.order_id)
            if user is None or order is None or order.user_id != user.id:
                raise PaymentValidationError("order_not_found")
            purchase = await BillingService(
                session,
                build_subscription_service(
                    session,
                    remnawave_client,
                    subscription_cipher,
                    remnawave_internal_squad_uuid,
                    remnawave_russia_squad_uuid,
                    remnawave_template_user_uuid,
                ),
            ).purchase_order(order.id, user_id=user.id)
            if purchase.purchased:
                await callback.answer("Средств уже достаточно.", show_alert=True)
                return
            payment = await _create_wallet_payment(
                session=session,
                user_id=user.id,
                amount=purchase.shortfall,
                payment_client=payment_client,
                payment_return_url=payment_return_url,
                public_base_url=public_base_url,
            )
    except (BillingValidationError, PaymentValidationError, YooKassaError):
        await callback.answer("Не удалось создать пополнение.", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            f"Пополнение баланса на {money(payment.amount)} создано.",
            build_payment(payment),
        )


@router.callback_query(
    F.data == "balance_topup"
)
async def request_wallet_topup(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(WalletTopUp.amount)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите сумму пополнения в рублях, например: 150"
        )


@router.callback_query(OrderCallback.filter(F.action == "topup_other"))
async def request_other_topup(
    callback: CallbackQuery, state: FSMContext
) -> None:
    await state.set_state(WalletTopUp.amount)
    await callback.answer()
    if callback.message:
        await callback.message.answer(
            "Введите другую сумму пополнения в рублях, например: 150"
        )


@router.message(WalletTopUp.amount)
async def create_wallet_topup(
    message: Message,
    state: FSMContext,
    session_factory: async_sessionmaker[AsyncSession],
    payment_client: YooKassaClient,
    payment_return_url: str | None,
    public_base_url: str | None,
) -> None:
    try:
        amount = Decimal((message.text or "").replace(",", ".")).quantize(
            Decimal("0.01")
        )
        if amount < Decimal("1.00") or amount > Decimal("100000.00"):
            raise InvalidOperation
    except (InvalidOperation, ValueError):
        await message.answer("Введите сумму от 1 до 100 000 ₽.")
        return
    try:
        async with session_factory() as session, session.begin():
            user = await UserRepository(session).get_by_telegram_id(
                message.from_user.id
            )
            if user is None:
                raise PaymentValidationError("user_not_found")
            payment = await _create_wallet_payment(
                session=session,
                user_id=user.id,
                amount=amount,
                payment_client=payment_client,
                payment_return_url=payment_return_url,
                public_base_url=public_base_url,
            )
    except (PaymentValidationError, YooKassaError):
        await message.answer("Не удалось создать пополнение. Попробуйте позднее.")
        return
    await state.clear()
    await message.answer(
        f"Пополнение на {money(payment.amount)} создано.",
        reply_markup=build_payment(payment),
    )


@router.callback_query(OrderCallback.filter(F.action == "pay"))
async def create_payment(
    callback: CallbackQuery,
    callback_data: OrderCallback,
    session_factory: async_sessionmaker[AsyncSession],
    payment_client: YooKassaClient,
    payment_return_url: str | None,
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
                session,
                payment_client,
                public_base_url=public_base_url,
                return_url=payment_return_url,
            ).create_for_order(callback_data.order_id, user_id=user.id)
    except YooKassaError:
        await callback.answer(
            "ЮKassa временно недоступна. Попробуйте ещё раз позднее.",
            show_alert=True,
        )
        return
    except PaymentValidationError:
        await callback.answer("Заказ нельзя оплатить", show_alert=True)
        return
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            "Платёж создан. Сумма и заказ проверены сервером.",
            build_payment(payment),
        )


@router.callback_query(OrderCallback.filter(F.action == "check"))
async def check_payment(
    callback: CallbackQuery,
    callback_data: OrderCallback,
    session_factory: async_sessionmaker[AsyncSession],
    payment_client: YooKassaClient,
    payment_return_url: str | None,
    public_base_url: str | None,
    redis_client: Redis,
    admin_ids: set[int],
    remnawave_client: RemnawaveClient | None = None,
    subscription_cipher: SubscriptionUrlCipher | None = None,
    remnawave_internal_squad_uuid: str | None = None,
    remnawave_russia_squad_uuid: str | None = None,
    remnawave_template_user_uuid: str | None = None,
) -> None:
    if callback.message is None or callback.message.chat.type != ChatType.PRIVATE:
        await callback.answer(
            "Для безопасности откройте бота в личных сообщениях.", show_alert=True
        )
        return
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
                payment_client,
                public_base_url=public_base_url,
                return_url=payment_return_url,
                subscription_service=build_subscription_service(
                    session,
                    remnawave_client,
                    subscription_cipher,
                    remnawave_internal_squad_uuid,
                    remnawave_russia_squad_uuid,
                    remnawave_template_user_uuid,
                ),
            ).check_status(payment, user_id=user.id)
    except (ValueError, PaymentValidationError):
        await callback.answer("Платёж не найден", show_alert=True)
        return
    except YooKassaError:
        await callback.answer(
            "Не удалось проверить платёж в ЮKassa. Попробуйте позднее.",
            show_alert=True,
        )
        return
    if result is None:
        await callback.answer("Оплата пока не найдена", show_alert=True)
    elif result.completed:
        await callback.answer("Оплата подтверждена", show_alert=True)
        if callback.message:
            balance = (
                result.balance_after
                if result.balance_after is not None
                else result.payment.amount
            )
            await callback.message.answer(
                "✅ Баланс пополнен\n\n"
                f"Сумма:\n{result.payment.amount:.2f} ₽\n\n"
                f"Текущий баланс:\n{balance:.2f} ₽"
            )
            if result.order.purpose == OrderPurpose.wallet_topup:
                await callback.message.answer(
                    "Пополнение кошелька завершено. Для продления VPN "
                    "подтвердите покупку тарифа отдельно."
                )
            elif result.subscription is not None:
                await send_activation_notification(
                    session_factory,
                    bot=callback.bot,
                    subscription_id=result.subscription.id,
                    cipher=subscription_cipher,
                )
    else:
        await callback.answer(
            "Оплата подтверждена, активация будет повторена автоматически.",
            show_alert=True,
        )
        if callback.message and result.balance_after is not None:
            await callback.message.answer(
                "✅ Баланс пополнен\n\n"
                f"Сумма:\n{result.payment.amount:.2f} ₽\n\n"
                f"Текущий баланс:\n{result.balance_after:.2f} ₽"
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
