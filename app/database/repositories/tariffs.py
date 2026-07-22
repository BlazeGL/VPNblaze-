from typing import Any

from sqlalchemy import delete, exists, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Order, Tariff


class TariffHasOrdersError(ValueError):
    pass


class TariffRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_id(self, tariff_id: int) -> Tariff | None:
        return await self.session.get(Tariff, tariff_id)

    async def get_active(self) -> list[Tariff]:
        result = await self.session.scalars(
            select(Tariff)
            .where(Tariff.is_active.is_(True))
            .order_by(Tariff.sort_order, Tariff.id)
        )
        return list(result)

    async def get_all(self) -> list[Tariff]:
        result = await self.session.scalars(
            select(Tariff).order_by(Tariff.sort_order, Tariff.id)
        )
        return list(result)

    async def create(self, **values: Any) -> Tariff:
        tariff = Tariff(**values)
        self.session.add(tariff)
        await self.session.flush()
        return tariff

    async def update(self, tariff: Tariff | int, **values: Any) -> Tariff:
        entity = await self.get_by_id(tariff) if isinstance(tariff, int) else tariff
        if entity is None:
            raise LookupError("Tariff not found")
        for key, value in values.items():
            if not hasattr(entity, key):
                raise ValueError(f"Unknown tariff field: {key}")
            setattr(entity, key, value)
        await self.session.flush()
        return entity

    async def activate(self, tariff_id: int) -> Tariff:
        return await self.update(tariff_id, is_active=True)

    async def deactivate(self, tariff_id: int) -> Tariff:
        return await self.update(tariff_id, is_active=False)

    async def delete(self, tariff_id: int) -> None:
        has_orders = await self.session.scalar(
            select(exists().where(Order.tariff_id == tariff_id))
        )
        if has_orders:
            raise TariffHasOrdersError("Нельзя удалить тариф, по которому есть заказы")
        result = await self.session.execute(
            delete(Tariff).where(Tariff.id == tariff_id)
        )
        if not result.rowcount:
            raise LookupError("Tariff not found")
