from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SubscriptionUrlCipher
from app.database.models import (
    ProvisioningStatus,
    Subscription,
    User,
)
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.remnawave.exceptions import (
    RemnawaveConflictError,
    RemnawaveNotFoundError,
)


@dataclass
class SyncSummary:
    checked: int = 0
    synchronized: int = 0
    errors: int = 0
    missing: int = 0
    conflicts: int = 0


class RemnawaveSyncService:
    def __init__(
        self,
        session: AsyncSession,
        client: RemnawaveClient,
        cipher: SubscriptionUrlCipher,
    ) -> None:
        self.session = session
        self.client = client
        self.cipher = cipher

    async def sync_one(self, subscription: Subscription, user: User) -> None:
        try:
            if subscription.remnawave_user_uuid:
                remote = await self.client.get_user(subscription.remnawave_user_uuid)
            elif subscription.remnawave_username:
                remote = await self.client.get_user_by_username(
                    subscription.remnawave_username
                )
            else:
                raise RemnawaveNotFoundError("Local Remnawave link is absent")
            if (
                remote.username != subscription.remnawave_username
                or remote.telegram_id != user.telegram_id
            ):
                raise RemnawaveConflictError("Remnawave user ownership conflict")
            subscription.remnawave_user_uuid = str(remote.uuid)
            subscription.external_user_uuid = str(remote.uuid)
            subscription.remnawave_short_uuid = remote.short_uuid
            subscription.subscription_url_encrypted = self.cipher.encrypt(
                remote.subscription_url
            )
            subscription.remnawave_status = remote.status.value
            subscription.remnawave_last_sync_at = datetime.now(UTC)
            subscription.remnawave_sync_error = None
            subscription.used_traffic_bytes = remote.user_traffic.used_traffic_bytes
            subscription.provisioning_status = ProvisioningStatus.active
        except Exception as exc:
            subscription.remnawave_sync_error = str(exc)[:1000]
            raise

    async def sync_batch(self, *, limit: int = 100, offset: int = 0) -> SyncSummary:
        rows = (
            await self.session.execute(
                select(Subscription, User)
                .join(User, User.id == Subscription.user_id)
                .where(Subscription.remnawave_username.is_not(None))
                .order_by(Subscription.id)
                .offset(offset)
                .limit(min(max(limit, 1), 100))
            )
        ).all()
        summary = SyncSummary()
        for subscription, user in rows:
            summary.checked += 1
            try:
                await self.sync_one(subscription, user)
                summary.synchronized += 1
            except RemnawaveNotFoundError:
                summary.missing += 1
            except RemnawaveConflictError:
                summary.conflicts += 1
            except Exception:
                summary.errors += 1
        await self.session.flush()
        return summary
