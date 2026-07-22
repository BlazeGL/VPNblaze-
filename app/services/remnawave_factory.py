from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SubscriptionUrlCipher
from app.integrations.remnawave.client import RemnawaveClient
from app.services.remnawave import (
    RemnawaveProvisioningService,
    RemnawaveSubscriptionAdapter,
)
from app.services.subscriptions import (
    SubscriptionService,
    UnavailableSubscriptionAdapter,
)


def build_subscription_service(
    session: AsyncSession,
    client: RemnawaveClient | None,
    cipher: SubscriptionUrlCipher | None,
    internal_squad_uuid: str | None,
) -> SubscriptionService:
    if client is None or cipher is None or not internal_squad_uuid:
        return SubscriptionService(
            session,
            UnavailableSubscriptionAdapter("Remnawave provisioning is not configured"),
        )
    service = RemnawaveProvisioningService(session, client, cipher, internal_squad_uuid)
    return SubscriptionService(session, RemnawaveSubscriptionAdapter(service))
