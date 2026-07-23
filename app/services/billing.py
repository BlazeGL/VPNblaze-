from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    Order,
    OrderPurpose,
    OrderStatus,
    Subscription,
    SubscriptionStatus,
    User,
)
from app.services.balance import (
    BalanceService,
    InsufficientBalanceError,
    normalize_money,
)
from app.services.promos import PromoService, PromoValidationError
from app.services.subscriptions import SubscriptionService


@dataclass(frozen=True)
class BillingResult:
    purchased: bool
    already_processed: bool
    amount: Decimal
    balance_before: Decimal
    balance_after: Decimal
    shortfall: Decimal
    order: Order
    subscription: Subscription | None = None
    transaction: BalanceTransaction | None = None


class BillingValidationError(ValueError):
    pass


class BillingService:
    """Buy a fixed-term subscription from the shared user balance."""

    def __init__(
        self,
        session: AsyncSession,
        subscription_service: SubscriptionService | None = None,
    ) -> None:
        self.session = session
        self.subscription_service = subscription_service or SubscriptionService(
            session
        )

    async def purchase_order(
        self,
        order_id: object,
        *,
        user_id: int,
        idempotency_key: str | None = None,
        now: datetime | None = None,
        locked_order: Order | None = None,
        locked_user: User | None = None,
    ) -> BillingResult:
        moment = now or datetime.now(UTC)
        order = locked_order
        if order is None:
            order = await self.session.scalar(
                select(Order).where(Order.id == order_id).with_for_update()
            )
        if order is None or order.user_id != user_id:
            raise BillingValidationError("order_not_found")
        if order.purpose == OrderPurpose.wallet_topup:
            raise BillingValidationError("not_subscription_order")

        user = locked_user
        if user is None:
            user = await self.session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
        if user is None:
            raise BillingValidationError("user_not_found")

        amount = self._purchase_amount(order)
        balance_before = normalize_money(user.balance)
        if order.status == OrderStatus.completed:
            subscription = await self.session.scalar(
                select(Subscription).where(Subscription.user_id == user.id)
            )
            return BillingResult(
                purchased=True,
                already_processed=True,
                amount=amount,
                balance_before=balance_before,
                balance_after=balance_before,
                shortfall=Decimal("0.00"),
                order=order,
                subscription=subscription,
            )
        if order.status not in {
            OrderStatus.pending,
            OrderStatus.awaiting_payment,
            OrderStatus.paid,
            OrderStatus.processing,
        }:
            raise BillingValidationError("order_status")

        key = idempotency_key or f"subscription-purchase:{order.id}"
        try:
            await PromoService(self.session).consume_for_paid_order(order)
        except PromoValidationError as exc:
            raise BillingValidationError(
                f"promo_consumption_failed:{exc.reason}"
            ) from exc
        try:
            change = await BalanceService(self.session).debit(
                user.id,
                amount=amount,
                transaction_type=BalanceTransactionType.subscription_purchase,
                idempotency_key=key,
                reference_type="order",
                reference_id=str(order.id),
                tariff_id=order.tariff_id,
                order_id=order.id,
                description=(
                    f"Покупка подписки на {order.duration_days_snapshot} дней"
                ),
                locked_user=user,
            )
        except InsufficientBalanceError:
            current = normalize_money(user.balance)
            return BillingResult(
                purchased=False,
                already_processed=False,
                amount=amount,
                balance_before=current,
                balance_after=current,
                shortfall=normalize_money(amount - current),
                order=order,
                subscription=None,
            )

        order.status = OrderStatus.processing
        subscription = await self.subscription_service.extend_from_paid_order(
            user, order, now=moment
        )
        if subscription.status != SubscriptionStatus.activation_failed:
            order.status = OrderStatus.completed
            order.completed_at = moment
            order.failure_reason = None
        else:
            order.failure_reason = (
                subscription.last_activation_error or "activation_failed"
            )
        await self.session.flush()
        return BillingResult(
            purchased=order.status == OrderStatus.completed,
            already_processed=change.already_applied,
            amount=amount,
            balance_before=change.transaction.balance_before,
            balance_after=change.transaction.balance_after,
            shortfall=Decimal("0.00"),
            order=order,
            subscription=subscription,
            transaction=change.transaction,
        )

    @staticmethod
    def _purchase_amount(order: Order) -> Decimal:
        original = normalize_money(order.original_amount or order.amount_snapshot)
        discount = normalize_money(order.discount_amount or 0)
        amount = normalize_money(original - discount)
        if amount <= 0:
            raise BillingValidationError("invalid_purchase_amount")
        return amount
