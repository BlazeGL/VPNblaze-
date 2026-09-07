from dataclasses import dataclass
from decimal import Decimal

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import (
    BalanceTransaction,
    BalanceTransactionType,
    User,
)
from app.services.balance import BalanceService

REFERRAL_BONUS = Decimal("50.00")
REFERRAL_PREFIX = "REF_"


@dataclass(frozen=True)
class ReferralResult:
    awarded: bool
    referrer: User | None = None
    transaction: BalanceTransaction | None = None
    reason: str | None = None


@dataclass(frozen=True)
class ReferralStats:
    total_referrals: int
    total_awarded: Decimal
    top_referrers: list[tuple[User, int]]


class ReferralService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @staticmethod
    def code_from_payload(payload: str | None) -> str | None:
        value = (payload or "").strip()
        if value.startswith(REFERRAL_PREFIX):
            value = value[len(REFERRAL_PREFIX) :]
        return value if value and len(value) <= 32 else None

    @staticmethod
    def deep_link(bot_username: str, referral_code: str) -> str:
        username = bot_username.removeprefix("@")
        return f"https://t.me/{username}?start={REFERRAL_PREFIX}{referral_code}"

    async def award_registration_bonus(
        self,
        invitee: User,
        payload: str | None,
    ) -> ReferralResult:
        code = self.code_from_payload(payload)
        if code is None:
            return ReferralResult(False, reason="missing_code")

        locked_invitee = await self.session.scalar(
            select(User).where(User.id == invitee.id).with_for_update()
        )
        if locked_invitee is None:
            return ReferralResult(False, reason="invitee_not_found")
        if locked_invitee.referred_by is not None:
            return ReferralResult(False, reason="already_referred")

        referrer = await self.session.scalar(
            select(User).where(User.referral_code == code)
        )
        if referrer is None:
            return ReferralResult(False, reason="invalid_code")
        if referrer.id == locked_invitee.id:
            return ReferralResult(False, reason="self_referral")

        change = await BalanceService(self.session).credit(
            referrer.id,
            amount=REFERRAL_BONUS,
            transaction_type=BalanceTransactionType.referral_bonus,
            idempotency_key=f"referral:{locked_invitee.id}",
            reference_type="user",
            reference_id=str(locked_invitee.id),
        )
        locked_invitee.referred_by = referrer.id
        if not change.already_applied:
            referrer.total_referrals += 1
            referrer.total_referral_income += REFERRAL_BONUS
        await self.session.flush()
        return ReferralResult(
            True,
            referrer=referrer,
            transaction=change.transaction,
        )

    async def global_stats(self, *, limit: int = 10) -> ReferralStats:
        total_referrals = (
            await self.session.scalar(
                select(func.count(User.id)).where(User.referred_by.is_not(None))
            )
            or 0
        )
        total_awarded = (
            await self.session.scalar(
                select(func.coalesce(func.sum(User.total_referral_income), 0))
            )
            or Decimal("0.00")
        )
        top = (
            await self.session.execute(
                select(User, User.total_referrals)
                .where(User.total_referrals > 0)
                .order_by(
                    User.total_referrals.desc(),
                    User.total_referral_income.desc(),
                    User.id,
                )
                .limit(max(1, min(limit, 50)))
            )
        ).all()
        return ReferralStats(
            total_referrals=int(total_referrals),
            total_awarded=Decimal(total_awarded),
            top_referrers=[(user, int(count)) for user, count in top],
        )
