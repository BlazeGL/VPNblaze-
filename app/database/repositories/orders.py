import uuid
from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, OrderPurpose, OrderStatus, Tariff


class OrderOwnershipError(PermissionError):
    pass


class OrderRepository:
    CANCELLABLE = {OrderStatus.pending, OrderStatus.awaiting_payment}

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_from_tariff(self, user_id: int, tariff: Tariff) -> Order:
        order = Order(
            user_id=user_id,
            tariff_id=tariff.id,
            status=OrderStatus.pending,
            tariff_name_snapshot=tariff.name,
            duration_days_snapshot=tariff.duration_days,
            traffic_limit_gb_snapshot=tariff.traffic_limit_gb,
            is_unlimited_traffic_snapshot=tariff.is_unlimited_traffic,
            device_limit_snapshot=tariff.device_limit,
            amount_snapshot=tariff.price,
            currency_snapshot=tariff.currency,
            original_amount=tariff.price,
            discount_amount=0,
            final_amount=tariff.price,
            bonus_days=0,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def create_wallet_topup(
        self,
        user_id: int,
        amount: object,
        *,
        currency: str = "RUB",
    ) -> Order:
        order = Order(
            user_id=user_id,
            tariff_id=None,
            purpose=OrderPurpose.wallet_topup,
            status=OrderStatus.pending,
            tariff_name_snapshot="Пополнение баланса",
            duration_days_snapshot=0,
            traffic_limit_gb_snapshot=None,
            is_unlimited_traffic_snapshot=False,
            device_limit_snapshot=1,
            amount_snapshot=amount,
            currency_snapshot=currency,
            original_amount=amount,
            discount_amount=0,
            final_amount=amount,
            bonus_days=0,
        )
        self.session.add(order)
        await self.session.flush()
        return order

    async def get_by_id(self, order_id: uuid.UUID | str) -> Order | None:
        try:
            parsed = uuid.UUID(str(order_id))
        except ValueError:
            return None
        return await self.session.get(Order, parsed)

    async def get_user_orders(self, user_id: int) -> list[Order]:
        result = await self.session.scalars(
            select(Order)
            .where(Order.user_id == user_id)
            .order_by(Order.created_at.desc())
        )
        return list(result)

    async def get_latest_pending_for_user(self, user_id: int) -> Order | None:
        return await self.session.scalar(
            select(Order)
            .where(Order.user_id == user_id, Order.status.in_(self.CANCELLABLE))
            .order_by(Order.created_at.desc())
            .limit(1)
        )

    async def update_status(
        self, order: Order | uuid.UUID | str, status: OrderStatus
    ) -> Order:
        entity = await self.get_by_id(order) if not isinstance(order, Order) else order
        if entity is None:
            raise LookupError("Order not found")
        entity.status = status
        now = datetime.now(UTC)
        if status == OrderStatus.paid:
            entity.paid_at = now
        elif status == OrderStatus.completed:
            entity.completed_at = now
        await self.session.flush()
        return entity

    async def cancel(self, order_id: uuid.UUID | str, user_id: int) -> Order:
        order = await self.get_by_id(order_id)
        if order is None:
            raise LookupError("Order not found")
        if order.user_id != user_id:
            raise OrderOwnershipError("Order belongs to another user")
        if order.status in self.CANCELLABLE:
            await self.update_status(order, OrderStatus.cancelled)
        return order

    async def expire_old_pending_orders(self, now: datetime | None = None) -> int:
        moment = now or datetime.now(UTC)
        result = await self.session.execute(
            update(Order)
            .where(
                Order.status.in_(self.CANCELLABLE),
                Order.expires_at.is_not(None),
                Order.expires_at <= moment,
            )
            .values(status=OrderStatus.expired, updated_at=moment)
        )
        return result.rowcount or 0
