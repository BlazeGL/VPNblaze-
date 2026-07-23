import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BalanceTransactionType,
    Order,
    OrderPurpose,
    OrderStatus,
    Payment,
    PaymentStatus,
    Subscription,
    SubscriptionStatus,
    Tariff,
    User,
)
from app.integrations.payments import (
    CreatePaymentCommand,
    NormalizedPaymentStatus,
    PaymentProviderClient,
    PaymentStatusResult,
)
from app.services.audit import add_audit_log, sanitize_details
from app.services.balance import BalanceService
from app.services.billing import BillingService
from app.services.promos import PromoService, PromoValidationError
from app.services.subscriptions import SubscriptionService


class PaymentValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class PaymentProcessingResult:
    completed: bool
    already_processed: bool
    order: Order
    payment: Payment
    subscription: Subscription | None = None
    failure_reason: str | None = None
    balance_after: Decimal | None = None


PROVIDER_TO_DB_STATUS = {
    NormalizedPaymentStatus.created: PaymentStatus.created,
    NormalizedPaymentStatus.pending: PaymentStatus.pending,
    NormalizedPaymentStatus.paid: PaymentStatus.paid,
    NormalizedPaymentStatus.failed: PaymentStatus.failed,
    NormalizedPaymentStatus.cancelled: PaymentStatus.cancelled,
    NormalizedPaymentStatus.expired: PaymentStatus.expired,
    NormalizedPaymentStatus.refunded: PaymentStatus.refunded,
}


class PaymentService:
    ACTIVE_STATUSES = (PaymentStatus.created, PaymentStatus.pending)

    def __init__(
        self,
        session: AsyncSession,
        client: PaymentProviderClient,
        *,
        public_base_url: str | None = None,
        return_url: str | None = None,
        subscription_service: SubscriptionService | None = None,
    ) -> None:
        self.session = session
        self.client = client
        self.public_base_url = public_base_url.rstrip("/") if public_base_url else None
        self.return_url = return_url or self.public_base_url
        self.subscription_service = subscription_service or SubscriptionService(session)

    async def get_active_for_order(self, order_id: uuid.UUID) -> Payment | None:
        return await self.session.scalar(
            select(Payment)
            .where(
                Payment.order_id == order_id,
                Payment.status.in_(self.ACTIVE_STATUSES),
            )
            .order_by(Payment.created_at.desc())
            .limit(1)
        )

    async def create_for_order(
        self,
        order_id: uuid.UUID | str,
        *,
        user_id: int,
    ) -> Payment:
        try:
            parsed_order_id = uuid.UUID(str(order_id))
        except ValueError as exc:
            raise PaymentValidationError("order_not_found") from exc
        order = await self.session.scalar(
            select(Order).where(Order.id == parsed_order_id).with_for_update()
        )
        if order is None or order.user_id != user_id:
            raise PaymentValidationError("order_not_found")
        if order.status not in {OrderStatus.pending, OrderStatus.awaiting_payment}:
            raise PaymentValidationError("order_status")
        existing = await self.get_active_for_order(order.id)
        if existing is not None:
            return existing

        original = Decimal(order.original_amount or order.amount_snapshot)
        order.original_amount = original
        tariff = (
            await self.session.get(Tariff, order.tariff_id)
            if order.tariff_id is not None
            else None
        )
        if (
            order.purpose != OrderPurpose.wallet_topup
            and tariff is None
        ):
            raise PaymentValidationError("tariff_not_found")
        if order.purpose == OrderPurpose.wallet_topup:
            order.discount_amount = Decimal("0.00")
            order.final_amount = original
            order.bonus_days = 0
        elif order.promo_code_id is not None:
            promo = await PromoService(self.session).get_by_code(
                order.promo_snapshot_code or "", for_update=True
            )
            if promo is None or promo.id != order.promo_code_id:
                raise PaymentValidationError("promo_not_found")
            application = await PromoService(self.session).validate(
                promo,
                user_id=order.user_id,
                tariff_id=tariff.id,
                amount=original,
            )
            order.discount_amount = application.discount_amount
            order.final_amount = application.final_amount
            order.bonus_days = application.bonus_days
            order.promo_snapshot_code = promo.code
            order.promo_snapshot_type = promo.discount_type.value
            order.promo_snapshot_value = promo.discount_value
        else:
            order.discount_amount = Decimal("0.00")
            order.final_amount = original
            order.bonus_days = 0

        attempt_number = (
            await self.session.scalar(
                select(func.count(Payment.id)).where(Payment.order_id == order.id)
            )
            or 0
        ) + 1
        provider_name = self.client.provider_name
        idempotency_key = str(
            uuid.uuid5(
                uuid.NAMESPACE_URL,
                f"{provider_name}:{order.id}:{attempt_number}",
            )
        )
        webhook_url = (
            f"{self.public_base_url}/api/webhooks/{provider_name}"
            if self.public_base_url
            else None
        )
        created = await self.client.create_payment(
            CreatePaymentCommand(
                order_id=str(order.id),
                amount=order.final_amount,
                currency=order.currency_snapshot,
                idempotency_key=idempotency_key,
                return_url=self.return_url,
                webhook_url=webhook_url,
            )
        )
        if not created.provider_payment_id or not created.payment_url:
            raise PaymentValidationError("invalid_provider_response")
        if created.status not in {
            NormalizedPaymentStatus.created,
            NormalizedPaymentStatus.pending,
        }:
            raise PaymentValidationError("invalid_provider_status")
        payment = Payment(
            order_id=order.id,
            provider=provider_name,
            provider_payment_id=created.provider_payment_id,
            status=PROVIDER_TO_DB_STATUS[created.status],
            amount=order.final_amount,
            currency=order.currency_snapshot,
            payment_url=created.payment_url,
            provider_payload_sanitized=sanitize_details(created.sanitized_payload),
            idempotency_key=idempotency_key,
        )
        self.session.add(payment)
        order.status = OrderStatus.awaiting_payment
        await self.session.flush()
        return payment

    async def check_status(
        self, payment: Payment, *, user_id: int
    ) -> PaymentProcessingResult | None:
        order = await self.session.get(Order, payment.order_id)
        if order is None or order.user_id != user_id:
            raise PaymentValidationError("order_not_found")
        result = await self.client.get_payment_status(payment.provider_payment_id)
        self._validate_provider_result(payment, order, result)
        if result.status != NormalizedPaymentStatus.paid:
            mapped = PROVIDER_TO_DB_STATUS.get(result.status)
            if mapped is not None:
                payment.status = mapped
                payment.provider_payload_sanitized = sanitize_details(
                    result.sanitized_payload
                )
                await self.session.flush()
            return None
        return await self.process_confirmed_payment(
            provider_payment_id=result.provider_payment_id,
            reported_order_id=result.order_id,
            amount=result.amount,
            currency=result.currency,
            sanitized_payload=result.sanitized_payload,
            webhook_received=False,
        )

    def _validate_provider_result(
        self, payment: Payment, order: Order, result: PaymentStatusResult
    ) -> None:
        if result.provider_payment_id != payment.provider_payment_id:
            raise PaymentValidationError("provider_payment_id_mismatch")
        if result.order_id != str(order.id):
            raise PaymentValidationError("order_id_mismatch")
        if result.amount != payment.amount or result.amount != order.final_amount:
            raise PaymentValidationError("amount_mismatch")
        if result.currency.upper() != payment.currency.upper():
            raise PaymentValidationError("currency_mismatch")

    async def record_provider_status(
        self,
        *,
        provider_payment_id: str,
        reported_order_id: str,
        amount: Decimal,
        currency: str,
        status: NormalizedPaymentStatus,
        sanitized_payload: dict[str, object] | None = None,
        now: datetime | None = None,
    ) -> Payment:
        if status == NormalizedPaymentStatus.paid:
            raise PaymentValidationError("paid_status_requires_processing")
        mapped = PROVIDER_TO_DB_STATUS.get(status)
        if mapped is None:
            raise PaymentValidationError("unknown_status")
        moment = now or datetime.now(UTC)
        payment = await self.session.scalar(
            select(Payment)
            .where(Payment.provider_payment_id == provider_payment_id)
            .with_for_update()
        )
        if payment is None:
            raise PaymentValidationError("payment_not_found")
        order = await self.session.scalar(
            select(Order).where(Order.id == payment.order_id).with_for_update()
        )
        if order is None:
            raise PaymentValidationError("order_not_found")
        if reported_order_id != str(order.id):
            raise PaymentValidationError("order_id_mismatch")
        if amount != payment.amount or amount != order.final_amount:
            raise PaymentValidationError("amount_mismatch")
        if currency.upper() != payment.currency.upper() or (
            currency.upper() != order.currency_snapshot.upper()
        ):
            raise PaymentValidationError("currency_mismatch")
        payment.webhook_received_at = moment
        if sanitized_payload is not None:
            payment.provider_payload_sanitized = sanitize_details(sanitized_payload)
        if payment.status != PaymentStatus.paid:
            payment.status = mapped
        if (
            order.status == OrderStatus.completed
            and payment.processed_at is not None
        ):
            add_audit_log(
                self.session,
                action="payment_repeated_webhook",
                entity_type="payment",
                entity_id=payment.id,
                actor_user_id=order.user_id,
            )
        await self.session.flush()
        return payment

    async def process_confirmed_payment(
        self,
        *,
        provider_payment_id: str,
        reported_order_id: str,
        amount: Decimal,
        currency: str,
        sanitized_payload: dict[str, object] | None = None,
        webhook_received: bool = True,
        now: datetime | None = None,
    ) -> PaymentProcessingResult:
        moment = now or datetime.now(UTC)
        payment = await self.session.scalar(
            select(Payment)
            .where(Payment.provider_payment_id == provider_payment_id)
            .with_for_update()
        )
        if payment is None:
            raise PaymentValidationError("payment_not_found")
        order = await self.session.scalar(
            select(Order).where(Order.id == payment.order_id).with_for_update()
        )
        if order is None:
            raise PaymentValidationError("order_not_found")
        if reported_order_id != str(order.id):
            raise PaymentValidationError("order_id_mismatch")
        if amount != payment.amount or amount != order.final_amount:
            raise PaymentValidationError("amount_mismatch")
        if currency.upper() != payment.currency.upper() or (
            currency.upper() != order.currency_snapshot.upper()
        ):
            raise PaymentValidationError("currency_mismatch")

        if webhook_received:
            payment.webhook_received_at = moment
        if sanitized_payload is not None:
            payment.provider_payload_sanitized = sanitize_details(sanitized_payload)
        if order.status == OrderStatus.completed:
            add_audit_log(
                self.session,
                action="payment_repeated_webhook",
                entity_type="payment",
                entity_id=payment.id,
                actor_user_id=order.user_id,
            )
            await self.session.flush()
            return PaymentProcessingResult(True, True, order, payment)

        order_already_purchased = order.status == OrderStatus.completed
        payment.status = PaymentStatus.paid
        payment.paid_at = payment.paid_at or moment
        order.paid_at = order.paid_at or moment
        if not order_already_purchased:
            order.status = OrderStatus.processing

        if order.purpose != OrderPurpose.wallet_topup:
            try:
                await PromoService(self.session).consume_for_paid_order(order)
            except PromoValidationError as exc:
                failure = f"promo_consumption_failed:{exc.reason}"
                order.failure_reason = failure
                payment.failure_reason = failure
                add_audit_log(
                    self.session,
                    action="payment_processing_error",
                    entity_type="payment",
                    entity_id=payment.id,
                    actor_user_id=order.user_id,
                    details={"reason": failure},
                )
                await self.session.flush()
                return PaymentProcessingResult(
                    False, False, order, payment, failure_reason=failure
                )

        user = await self.session.get(User, order.user_id)
        if user is None:
            raise PaymentValidationError("user_not_found")
        if user.balance is None:
            user.balance = Decimal("0.00")
        topup = await BalanceService(self.session).credit(
            user.id,
            amount=payment.amount,
            transaction_type=BalanceTransactionType.topup,
            idempotency_key=f"payment:{payment.id}",
            reference_type="payment",
            reference_id=str(payment.id),
            locked_user=user,
        )
        if order.purpose == OrderPurpose.wallet_topup:
            order.status = OrderStatus.completed
            order.completed_at = moment
            order.failure_reason = None
            payment.processed_at = moment
            payment.failure_reason = None
            add_audit_log(
                self.session,
                action="balance_topup_succeeded",
                entity_type="payment",
                entity_id=payment.id,
                actor_user_id=order.user_id,
                details={
                    "order_id": str(order.id),
                    "amount": str(payment.amount),
                    "currency": payment.currency,
                },
            )
            await self.session.flush()
            return PaymentProcessingResult(
                True,
                False,
                order,
                payment,
                balance_after=topup.transaction.balance_after if topup else None,
            )

        if order_already_purchased:
            subscription = await self.subscription_service.get_for_update(user.id)
            payment.processed_at = moment
            payment.failure_reason = None
            add_audit_log(
                self.session,
                action="payment_credited_after_balance_purchase",
                entity_type="payment",
                entity_id=payment.id,
                actor_user_id=order.user_id,
                details={
                    "order_id": str(order.id),
                    "amount": str(payment.amount),
                    "currency": payment.currency,
                },
            )
            await self.session.flush()
            return PaymentProcessingResult(
                True,
                False,
                order,
                payment,
                subscription=subscription,
                balance_after=topup.transaction.balance_after,
            )

        purchase = await BillingService(
            self.session, self.subscription_service
        ).purchase_order(
            order.id,
            user_id=user.id,
            idempotency_key=f"subscription-purchase:{order.id}",
            now=moment,
            locked_order=order,
            locked_user=user,
        )
        subscription = purchase.subscription
        if subscription is None:
            raise PaymentValidationError("subscription_not_found")
        if subscription.status == SubscriptionStatus.activation_failed:
            failure = subscription.last_activation_error or "activation_failed"
            order.failure_reason = failure
            payment.failure_reason = failure
            add_audit_log(
                self.session,
                action="payment_processing_error",
                entity_type="payment",
                entity_id=payment.id,
                actor_user_id=order.user_id,
                details={"reason": failure},
            )
            await self.session.flush()
            return PaymentProcessingResult(
                False,
                False,
                order,
                payment,
                subscription,
                failure,
                purchase.balance_after,
            )

        order.status = OrderStatus.completed
        order.completed_at = moment
        order.failure_reason = None
        payment.processed_at = moment
        payment.failure_reason = None
        add_audit_log(
            self.session,
            action="payment_succeeded",
            entity_type="payment",
            entity_id=payment.id,
            actor_user_id=order.user_id,
            details={
                "order_id": str(order.id),
                "amount": str(payment.amount),
                "currency": payment.currency,
            },
        )
        await self.session.flush()
        return PaymentProcessingResult(
            True,
            False,
            order,
            payment,
            subscription=subscription,
            balance_after=purchase.balance_after,
        )
