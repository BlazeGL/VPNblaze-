import logging
from collections.abc import Mapping

from fastapi import APIRouter, BackgroundTasks, HTTPException, Request, status
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.core.config import Settings
from app.database.models import OrderPurpose, User
from app.integrations.payments import NormalizedPaymentStatus
from app.integrations.yookassa.client import YooKassaClient
from app.integrations.yookassa.exceptions import (
    YooKassaConfigurationError,
    YooKassaError,
    YooKassaRequestError,
)
from app.services.activation_notifications import send_activation_notification
from app.services.payments import PaymentService, PaymentValidationError
from app.services.remnawave_factory import build_subscription_service
from app.webhooks.onlipay import notify_user

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

SUPPORTED_EVENTS = {
    "payment.succeeded",
    "payment.canceled",
    "payment.waiting_for_capture",
}


def _payment_id_from_notification(payload: object) -> tuple[str, str] | None:
    if not isinstance(payload, Mapping):
        raise ValueError("notification must be an object")
    if payload.get("type") != "notification":
        raise ValueError("unsupported notification type")
    event = payload.get("event")
    payment = payload.get("object")
    if not isinstance(event, str) or not isinstance(payment, Mapping):
        raise ValueError("notification is incomplete")
    if event not in SUPPORTED_EVENTS:
        return None
    payment_id = payment.get("id")
    if not isinstance(payment_id, str) or not payment_id.strip():
        raise ValueError("payment ID is missing")
    return event, payment_id


@router.post("/yookassa", status_code=status.HTTP_200_OK)
async def yookassa_webhook(
    request: Request, background_tasks: BackgroundTasks
) -> dict[str, str]:
    settings: Settings = request.app.state.settings
    client: YooKassaClient = request.app.state.yookassa_client
    session_factory: async_sessionmaker[AsyncSession] = (
        request.app.state.session_factory
    )
    try:
        parsed = _payment_id_from_notification(await request.json())
    except (ValueError, TypeError) as exc:
        raise HTTPException(status_code=400, detail="Invalid notification") from exc
    if parsed is None:
        return {"status": "ignored"}

    event, payment_id = parsed
    try:
        # YooKassa does not sign Basic Auth notifications. Fetching the payment
        # through the authenticated API verifies ownership and current status.
        verified = await client.get_payment_status(payment_id)
    except YooKassaConfigurationError as exc:
        raise HTTPException(
            status_code=503, detail="YooKassa is not configured"
        ) from exc
    except YooKassaRequestError as exc:
        code = 502 if exc.retryable else 503
        raise HTTPException(
            status_code=code, detail="Could not verify YooKassa payment"
        ) from exc
    except YooKassaError as exc:
        raise HTTPException(
            status_code=502, detail="Invalid YooKassa response"
        ) from exc

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
            if verified.status == NormalizedPaymentStatus.paid:
                result = await service.process_confirmed_payment(
                    provider_payment_id=verified.provider_payment_id,
                    reported_order_id=verified.order_id,
                    amount=verified.amount,
                    currency=verified.currency,
                    sanitized_payload=verified.sanitized_payload,
                    webhook_received=True,
                )
                user = await session.get(User, result.order.user_id)
                telegram_id = user.telegram_id if user is not None else None
            else:
                await service.record_provider_status(
                    provider_payment_id=verified.provider_payment_id,
                    reported_order_id=verified.order_id,
                    amount=verified.amount,
                    currency=verified.currency,
                    status=verified.status,
                    sanitized_payload=verified.sanitized_payload,
                )
                logger.info(
                    "Accepted YooKassa notification event=%s payment_id=%s status=%s",
                    event,
                    payment_id,
                    verified.status,
                )
                return {"status": "accepted"}
    except PaymentValidationError as exc:
        logger.warning(
            "Rejected YooKassa notification payment_id=%s reason=%s",
            payment_id,
            exc.reason,
        )
        raise HTTPException(
            status_code=400, detail="Notification validation failed"
        ) from exc

    if telegram_id is not None and result.completed and not result.already_processed:
        balance = (
            result.balance_after
            if result.balance_after is not None
            else result.payment.amount
        )
        notification = (
            "✅ Баланс пополнен\n\n"
            f"Сумма:\n{result.payment.amount:.2f} ₽\n\n"
            f"Текущий баланс:\n{balance:.2f} ₽"
        )
        notification_markup = None
        if result.order.purpose == OrderPurpose.wallet_topup:
            notification = (
                f"{notification}\n\n"
                "Пополнение кошелька завершено. Для продления VPN "
                "подтвердите покупку тарифа отдельно."
            )
        background_tasks.add_task(
            notify_user,
            request.app.state.bot,
            telegram_id,
            notification,
            notification_markup,
        )
        if (
            result.order.purpose != OrderPurpose.wallet_topup
            and result.subscription is not None
        ):
            background_tasks.add_task(
                send_activation_notification,
                session_factory,
                bot=request.app.state.bot,
                subscription_id=result.subscription.id,
                cipher=request.app.state.subscription_cipher,
            )
    elif telegram_id is not None and not result.completed:
        balance_message = ""
        if result.balance_after is not None:
            balance_message = (
                "✅ Баланс пополнен\n\n"
                f"Сумма:\n{result.payment.amount:.2f} ₽\n\n"
                f"Текущий баланс:\n{result.balance_after:.2f} ₽\n\n"
            )
        background_tasks.add_task(
            notify_user,
            request.app.state.bot,
            telegram_id,
            f"{balance_message}"
            "✅ Оплата подтверждена. Активация будет повторена автоматически.",
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
