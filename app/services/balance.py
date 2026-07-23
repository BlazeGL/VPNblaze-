from dataclasses import dataclass
from decimal import Decimal, InvalidOperation
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    User,
)

MONEY_PLACES = Decimal("0.01")


class BalanceError(ValueError):
    pass


class InsufficientBalanceError(BalanceError):
    pass


@dataclass(frozen=True)
class BalanceChange:
    transaction: BalanceTransaction
    already_applied: bool


def normalize_money(value: Decimal | int | str) -> Decimal:
    try:
        amount = Decimal(value).quantize(MONEY_PLACES)
    except (InvalidOperation, ValueError) as exc:
        raise BalanceError("invalid_amount") from exc
    if not amount.is_finite():
        raise BalanceError("invalid_amount")
    return amount


class BalanceService:
    """The only application service allowed to mutate a user's balance."""

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def recent_transactions(
        self,
        user_id: int,
        *,
        limit: int = 5,
    ) -> list[BalanceTransaction]:
        """Return a bounded, newest-first balance history for user-facing screens."""
        safe_limit = max(1, min(limit, 50))
        return list(
            await self.session.scalars(
                select(BalanceTransaction)
                .where(BalanceTransaction.user_id == user_id)
                .order_by(
                    BalanceTransaction.created_at.desc(),
                    BalanceTransaction.id.desc(),
                )
                .limit(safe_limit)
            )
        )

    async def change(
        self,
        user_id: int,
        *,
        amount: Decimal | int | str,
        transaction_type: BalanceTransactionType,
        idempotency_key: str,
        reference_type: str | None = None,
        reference_id: str | None = None,
        tariff_id: int | None = None,
        order_id: UUID | None = None,
        description: str | None = None,
        locked_user: User | None = None,
    ) -> BalanceChange:
        normalized = normalize_money(amount)
        if normalized == 0:
            raise BalanceError("zero_amount")
        if not idempotency_key or len(idempotency_key) > 255:
            raise BalanceError("invalid_idempotency_key")

        user = locked_user
        if user is None:
            user = await self.session.scalar(
                select(User).where(User.id == user_id).with_for_update()
            )
        if user is None:
            raise BalanceError("user_not_found")
        if user.id != user_id:
            raise BalanceError("user_lock_mismatch")

        existing = await self.session.scalar(
            select(BalanceTransaction).where(
                BalanceTransaction.idempotency_key == idempotency_key
            )
        )
        if existing is not None:
            if (
                existing.user_id != user_id
                or existing.type != transaction_type
                or existing.amount != normalized
            ):
                raise BalanceError("idempotency_conflict")
            return BalanceChange(existing, True)

        before = normalize_money(user.balance)
        after = normalize_money(before + normalized)
        if after < 0:
            raise InsufficientBalanceError("insufficient_balance")

        transaction = BalanceTransaction(
            user_id=user.id,
            type=transaction_type,
            amount=normalized,
            balance_before=before,
            balance_after=after,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            tariff_id=tariff_id,
            order_id=order_id,
            description=description,
        )
        user.balance = after
        self.session.add(transaction)
        await self.session.flush()
        return BalanceChange(transaction, False)

    async def credit(
        self,
        user_id: int,
        *,
        amount: Decimal | int | str,
        transaction_type: BalanceTransactionType,
        idempotency_key: str,
        reference_type: str | None = None,
        reference_id: str | None = None,
        tariff_id: int | None = None,
        order_id: UUID | None = None,
        description: str | None = None,
        locked_user: User | None = None,
    ) -> BalanceChange:
        normalized = normalize_money(amount)
        if normalized <= 0:
            raise BalanceError("credit_must_be_positive")
        return await self.change(
            user_id,
            amount=normalized,
            transaction_type=transaction_type,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            tariff_id=tariff_id,
            order_id=order_id,
            description=description,
            locked_user=locked_user,
        )

    async def debit(
        self,
        user_id: int,
        *,
        amount: Decimal | int | str,
        transaction_type: BalanceTransactionType,
        idempotency_key: str,
        reference_type: str | None = None,
        reference_id: str | None = None,
        tariff_id: int | None = None,
        order_id: UUID | None = None,
        description: str | None = None,
        locked_user: User | None = None,
    ) -> BalanceChange:
        normalized = normalize_money(amount)
        if normalized <= 0:
            raise BalanceError("debit_must_be_positive")
        return await self.change(
            user_id,
            amount=-normalized,
            transaction_type=transaction_type,
            idempotency_key=idempotency_key,
            reference_type=reference_type,
            reference_id=reference_id,
            tariff_id=tariff_id,
            order_id=order_id,
            description=description,
            locked_user=locked_user,
        )
