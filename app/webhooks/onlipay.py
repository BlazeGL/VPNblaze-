import logging

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.types import InlineKeyboardMarkup
from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.subscription import activation_keyboard
from app.bot.texts.subscription import activation_text
from app.core.config import Settings
from app.integrations.onlipay.client import OnliPayClient
from app.integrations.onlipay.exceptions import (
    InvalidWebhookPayload,
    InvalidWebhookSignature,
    OnliPayUnavailableError,
)
from app.integrations.onlipay.schemas import NormalizedPaymentStatus
from app.integrations.onlipay.signature import OnliPayWebhookVerifier
from app.services.payments import PaymentService, PaymentValidationError
from app.services.remnawave_factory import build_subscription_service

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])


async def notify_user(
    bot: Bot,
    telegram_id: int,
    text: str,
    reply_markup: InlineKeyboardMarkup | None = None,
    parse_mode: ParseMode | None = None,
) -> None:
    try:
        await bot.send_message(
            telegram_id,
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
    except Exception:
        logger.exception("Could not send payment notification to user %s", telegram_id)


@router.post("/onlipay", status_code=status.HTTP_200_OK)
async def onlipay_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    verifier: OnliPayWebhookVerifier = request.app.state.onlipay_webhook_verifier
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.session_factory
    )
    client: OnliPayClient = request.app.state.onlipay_client
    body = await request.body()
    try:
        event = verifier.verify_and_decode(body, request.headers)
    except InvalidWebhookSignature as exc:
        raise HTTPException(status_code=401, detail="Invalid signature") from exc
    except InvalidWebhookPayload as exc:
        raise HTTPException(status_code=400, detail="Invalid payload") from exc
    except OnliPayUnavailableError as exc:
        raise HTTPException(
            status_code=503, detail="OnliPay webhook is not configured"
        ) from exc

    if not settings.onlipay_merchant_id:
        raise HTTPException(
            status_code=503, detail="OnliPay merchant is not configured"
        )
    if event.merchant_id != settings.onlipay_merchant_id:
        raise HTTPException(status_code=400, detail="Merchant mismatch")
    try:
        async with session_factory() as session, session.begin():
            service = PaymentService(
                session,
                client,
                public_base_url=settings.public_base_url,
                subscription_service=build_subscription_service(
                    session,
                    request.app.state.remnawave_client,
                    request.app.state.subscription_cipher,
                    settings.remnawave_internal_squad_uuid,
                ),
            )
            if event.status == NormalizedPaymentStatus.paid:
                result = await service.process_confirmed_payment(
                    provider_payment_id=event.provider_payment_id,
                    reported_order_id=event.order_id,
                    amount=event.amount,
                    currency=event.currency,
                    sanitized_payload=event.sanitized_payload,
                    webhook_received=True,
                )
                telegram_id = result.order.user_id
                from app.database.models import User

                user = await session.get(User, result.order.user_id)
                if user is not None:
                    telegram_id = user.telegram_id
            else:
                await service.record_provider_status(
                    provider_payment_id=event.provider_payment_id,
                    reported_order_id=event.order_id,
                    amount=event.amount,
                    currency=event.currency,
                    status=event.status,
                    sanitized_payload=event.sanitized_payload,
                )
                return {"status": "accepted"}
    except PaymentValidationError as exc:
        logger.warning("Rejected OnliPay webhook: %s", exc.reason)
        raise HTTPException(
            status_code=400, detail="Webhook validation failed"
        ) from exc

    if result.completed and not result.already_processed:
        notification = "✅ Оплата подтверждена. Подписка активирована."
        notification_markup = None
        notification_parse_mode = None
        if (
            result.subscription is not None
            and result.subscription.subscription_url_encrypted
            and request.app.state.subscription_cipher is not None
        ):
            url = request.app.state.subscription_cipher.decrypt(
                result.subscription.subscription_url_encrypted
            )
            notification = activation_text(result.subscription, url)
            notification_markup = activation_keyboard()
            notification_parse_mode = ParseMode.HTML
        background_tasks.add_task(
            notify_user,
            request.app.state.bot,
            telegram_id,
            notification,
            notification_markup,
            notification_parse_mode,
        )
    elif not result.completed:
        background_tasks.add_task(
            notify_user,
            request.app.state.bot,
            telegram_id,
            "✅ Оплата подтверждена. Активация подписки будет повторена автоматически.",
        )
        for admin_id in settings.admin_ids:
            background_tasks.add_task(
                notify_user,
                request.app.state.bot,
                admin_id,
                "⚠️ Ошибка активации оплаченного заказа. "
                f"Заказ: {str(result.order.id)[:8]}",
            )
    return {"status": "already_processed" if result.already_processed else "accepted"}
