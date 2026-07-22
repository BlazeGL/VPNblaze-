import re
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    Order,
    OrderStatus,
    PromoCode,
    PromoCodeTariff,
    PromoCodeUsage,
    PromoDiscountType,
    Tariff,
)
from app.services.audit import add_audit_log

MONEY_STEP = Decimal("0.01")
CODE_PATTERN = re.compile(r"^[A-ZА-ЯЁ0-9_-]{2,64}$")


class PromoValidationError(ValueError):
    def __init__(self, reason: str) -> None:
        self.reason = reason
        super().__init__(reason)


@dataclass(frozen=True)
class PromoApplication:
    promo_code: PromoCode
    original_amount: Decimal
    discount_amount: Decimal
    final_amount: Decimal
    bonus_days: int


def normalize_promo_code(code: str) -> str:
    return code.strip().upper()


def validate_code_format(code: str) -> str:
    normalized = normalize_promo_code(code)
    if not CODE_PATTERN.fullmatch(normalized):
        raise PromoValidationError("invalid_code_format")
    return normalized


def calculate_promo(promo: PromoCode, original_amount: Decimal) -> PromoApplication:
    amount = Decimal(original_amount).quantize(MONEY_STEP, rounding=ROUND_HALF_UP)
    if promo.discount_type == PromoDiscountType.percent:
        discount = (amount * promo.discount_value / Decimal("100")).quantize(
            MONEY_STEP, rounding=ROUND_HALF_UP
        )
        discount = min(amount, discount)
        bonus_days = 0
    elif promo.discount_type == PromoDiscountType.fixed:
        discount = min(
            amount,
            Decimal(promo.discount_value).quantize(MONEY_STEP, rounding=ROUND_HALF_UP),
        )
        bonus_days = 0
    else:
        discount = Decimal("0.00")
        bonus_days = promo.bonus_days or int(promo.discount_value)
    return PromoApplication(
        promo_code=promo,
        original_amount=amount,
        discount_amount=discount,
        final_amount=max(Decimal("0.00"), amount - discount),
        bonus_days=bonus_days,
    )


class PromoService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_code(
        self, code: str, *, for_update: bool = False
    ) -> PromoCode | None:
        normalized = normalize_promo_code(code)
        query = select(PromoCode).where(PromoCode.code == normalized)
        if for_update:
            query = query.with_for_update()
        return await self.session.scalar(query)

    async def validate(
        self,
        promo: PromoCode,
        *,
        user_id: int,
        tariff_id: int,
        amount: Decimal,
        now: datetime | None = None,
    ) -> PromoApplication:
        moment = now or datetime.now(UTC)
        if not promo.is_active:
            raise PromoValidationError("inactive")
        if promo.valid_from is not None and promo.valid_from > moment:
            raise PromoValidationError("not_started")
        if promo.valid_until is not None and promo.valid_until <= moment:
            raise PromoValidationError("expired")
        if promo.max_uses is not None and promo.uses_count >= promo.max_uses:
            raise PromoValidationError("max_uses_reached")
        if (
            promo.minimum_order_amount is not None
            and amount < promo.minimum_order_amount
        ):
            raise PromoValidationError("minimum_amount")

        tariff_restrictions = await self.session.scalar(
            select(func.count(PromoCodeTariff.tariff_id)).where(
                PromoCodeTariff.promo_code_id == promo.id
            )
        )
        if tariff_restrictions:
            applicable = await self.session.scalar(
                select(func.count(PromoCodeTariff.tariff_id)).where(
                    PromoCodeTariff.promo_code_id == promo.id,
                    PromoCodeTariff.tariff_id == tariff_id,
                )
            )
            if not applicable:
                raise PromoValidationError("tariff_not_applicable")

        user_uses = await self.session.scalar(
            select(func.count(PromoCodeUsage.id)).where(
                PromoCodeUsage.promo_code_id == promo.id,
                PromoCodeUsage.user_id == user_id,
            )
        )
        if (user_uses or 0) >= promo.per_user_limit:
            raise PromoValidationError("per_user_limit_reached")
        return calculate_promo(promo, amount)

    async def apply_to_order(
        self,
        order: Order,
        *,
        user_id: int,
        code: str,
        actor_telegram_id: int | None = None,
        now: datetime | None = None,
    ) -> PromoApplication:
        if order.user_id != user_id:
            raise PromoValidationError("foreign_order")
        if order.status not in {OrderStatus.pending, OrderStatus.awaiting_payment}:
            raise PromoValidationError("order_status")
        promo = await self.get_by_code(code, for_update=True)
        if promo is None:
            raise PromoValidationError("not_found")
        original = Decimal(order.original_amount or order.amount_snapshot)
        application = await self.validate(
            promo,
            user_id=user_id,
            tariff_id=order.tariff_id,
            amount=original,
            now=now,
        )
        order.promo_code_id = promo.id
        order.original_amount = application.original_amount
        order.discount_amount = application.discount_amount
        order.final_amount = application.final_amount
        order.bonus_days = application.bonus_days
        order.promo_snapshot_code = promo.code
        order.promo_snapshot_type = promo.discount_type.value
        order.promo_snapshot_value = promo.discount_value
        add_audit_log(
            self.session,
            action="promo_applied",
            entity_type="order",
            entity_id=order.id,
            actor_user_id=user_id,
            actor_telegram_id=actor_telegram_id,
            details={
                "promo_code": promo.code,
                "discount_amount": str(application.discount_amount),
                "bonus_days": application.bonus_days,
            },
        )
        await self.session.flush()
        return application

    async def consume_for_paid_order(self, order: Order) -> PromoCodeUsage | None:
        if order.promo_code_id is None:
            return None
        existing = await self.session.scalar(
            select(PromoCodeUsage).where(PromoCodeUsage.order_id == order.id)
        )
        if existing is not None:
            return existing
        promo = await self.session.scalar(
            select(PromoCode)
            .where(PromoCode.id == order.promo_code_id)
            .with_for_update()
        )
        if promo is None:
            raise PromoValidationError("not_found")
        if promo.max_uses is not None and promo.uses_count >= promo.max_uses:
            raise PromoValidationError("max_uses_reached")
        user_uses = await self.session.scalar(
            select(func.count(PromoCodeUsage.id)).where(
                PromoCodeUsage.promo_code_id == promo.id,
                PromoCodeUsage.user_id == order.user_id,
            )
        )
        if (user_uses or 0) >= promo.per_user_limit:
            raise PromoValidationError("per_user_limit_reached")
        usage = PromoCodeUsage(
            promo_code_id=promo.id,
            user_id=order.user_id,
            order_id=order.id,
            discount_amount=order.discount_amount,
            bonus_days=order.bonus_days,
        )
        self.session.add(usage)
        promo.uses_count += 1
        await self.session.flush()
        return usage

    async def create(
        self,
        *,
        code: str,
        discount_type: PromoDiscountType,
        discount_value: Decimal,
        bonus_days: int | None,
        max_uses: int | None,
        per_user_limit: int,
        minimum_order_amount: Decimal | None,
        valid_from: datetime | None,
        valid_until: datetime | None,
        created_by_admin_id: int,
        tariff_ids: list[int] | None,
        actor_telegram_id: int,
    ) -> PromoCode:
        normalized = validate_code_format(code)
        if await self.get_by_code(normalized) is not None:
            raise PromoValidationError("duplicate")
        if discount_type == PromoDiscountType.percent and not (
            Decimal("1") <= discount_value <= Decimal("100")
        ):
            raise PromoValidationError("percent_range")
        if discount_value <= 0:
            raise PromoValidationError("value_positive")
        if discount_type == PromoDiscountType.bonus_days and (
            bonus_days is None or bonus_days <= 0
        ):
            raise PromoValidationError("bonus_days_positive")
        promo = PromoCode(
            code=normalized,
            discount_type=discount_type,
            discount_value=discount_value,
            bonus_days=bonus_days,
            max_uses=max_uses,
            per_user_limit=per_user_limit,
            minimum_order_amount=minimum_order_amount,
            valid_from=valid_from,
            valid_until=valid_until,
            is_active=True,
            created_by_admin_id=created_by_admin_id,
        )
        self.session.add(promo)
        await self.session.flush()
        if tariff_ids:
            tariffs = list(
                await self.session.scalars(
                    select(Tariff.id).where(Tariff.id.in_(tariff_ids))
                )
            )
            if set(tariffs) != set(tariff_ids):
                raise PromoValidationError("tariff_not_found")
            self.session.add_all(
                [
                    PromoCodeTariff(promo_code_id=promo.id, tariff_id=tariff_id)
                    for tariff_id in tariff_ids
                ]
            )
        add_audit_log(
            self.session,
            action="promo_created",
            entity_type="promo_code",
            entity_id=promo.id,
            actor_user_id=created_by_admin_id,
            actor_telegram_id=actor_telegram_id,
            details={"code": promo.code, "type": promo.discount_type.value},
        )
        await self.session.flush()
        return promo

    async def set_active(
        self,
        promo_id: uuid.UUID,
        *,
        is_active: bool,
        actor_user_id: int,
        actor_telegram_id: int,
    ) -> PromoCode:
        promo = await self.session.scalar(
            select(PromoCode).where(PromoCode.id == promo_id).with_for_update()
        )
        if promo is None:
            raise PromoValidationError("not_found")
        promo.is_active = is_active
        add_audit_log(
            self.session,
            action="promo_enabled" if is_active else "promo_disabled",
            entity_type="promo_code",
            entity_id=promo.id,
            actor_user_id=actor_user_id,
            actor_telegram_id=actor_telegram_id,
            details={"code": promo.code},
        )
        await self.session.flush()
        return promo
