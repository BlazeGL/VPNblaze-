import logging
import secrets
import uuid
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.crypto import SubscriptionUrlCipher
from app.database.models import (
    ProvisioningOperation,
    ProvisioningOperationStatus,
    ProvisioningStatus,
    Subscription,
    SubscriptionSource,
    SubscriptionStatus,
    User,
)
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.remnawave.enums import (
    RemnawaveUserStatus,
    TrafficLimitStrategy,
)
from app.integrations.remnawave.exceptions import (
    RemnawaveAPIError,
    RemnawaveConfigurationError,
    RemnawaveConflictError,
    RemnawaveNetworkError,
    RemnawaveNotFoundError,
)
from app.integrations.remnawave.schemas import (
    CreateUserRequest,
    RemnawaveUser,
    UpdateUserRequest,
)
from app.services.audit import add_audit_log
from app.services.subscriptions import ProvisioningResult

logger = logging.getLogger(__name__)
RETRY_DELAYS = (60, 300, 900, 3600, 21600)


class RemnawaveProvisioningService:
    def __init__(
        self,
        session: AsyncSession,
        client: RemnawaveClient,
        cipher: SubscriptionUrlCipher,
        internal_squad_uuid: str | None,
    ) -> None:
        self.session = session
        self.client = client
        self.cipher = cipher
        self.internal_squad_uuid = internal_squad_uuid

    async def provision_user_subscription(
        self,
        user_id: int,
        source: SubscriptionSource | str,
        order_id: uuid.UUID | None = None,
    ) -> ProvisioningResult:
        user = await self.session.scalar(
            select(User).where(User.id == user_id).with_for_update()
        )
        subscription = await self.session.scalar(
            select(Subscription)
            .where(Subscription.user_id == user_id)
            .with_for_update()
        )
        if user is None or subscription is None:
            raise LookupError("Local user or subscription not found")
        return await self.provision(
            subscription, user, source=source, order_id=order_id
        )

    async def provision(
        self,
        subscription: Subscription,
        user: User,
        *,
        source: SubscriptionSource | str | None = None,
        order_id: uuid.UUID | None = None,
    ) -> ProvisioningResult:
        if not self.internal_squad_uuid:
            return await self._fail(
                subscription,
                user,
                None,
                RemnawaveConfigurationError(
                    "REMNAWAVE_INTERNAL_SQUAD_UUID is not configured"
                ),
            )
        try:
            squad_uuid = uuid.UUID(self.internal_squad_uuid)
        except ValueError:
            return await self._fail(
                subscription,
                user,
                None,
                RemnawaveConfigurationError("REMNAWAVE_INTERNAL_SQUAD_UUID is invalid"),
            )

        operation = await self._get_or_create_operation(
            subscription, user, source or subscription.source_type, order_id
        )
        if operation.status == ProvisioningOperationStatus.completed:
            return ProvisioningResult(
                status=SubscriptionStatus.active,
                external_user_uuid=subscription.remnawave_user_uuid,
                subscription_url_encrypted=subscription.subscription_url_encrypted,
            )
        if subscription.provisioning_status == ProvisioningStatus.provisioning:
            return ProvisioningResult(status=SubscriptionStatus.pending)

        subscription.provisioning_status = ProvisioningStatus.provisioning
        operation.status = ProvisioningOperationStatus.processing
        operation.attempts += 1
        subscription.activation_attempts += 1
        await self.session.flush()

        if not subscription.remnawave_username:
            subscription.remnawave_username = (
                f"tg_{user.telegram_id}_{secrets.token_hex(3)}"
            )
            await self.session.flush()

        try:
            had_uuid = bool(subscription.remnawave_user_uuid)
            remote, created = await self._resolve_or_create(subscription, user)
            was_disabled = remote.status != RemnawaveUserStatus.active
            self._save_remote_identity(subscription, remote)
            await self.session.flush()
            remote = await self._configure_remote(
                remote, subscription, user, squad_uuid, created=created
            )
            self._validate_owner(remote, subscription, user)
            if squad_uuid not in {item.uuid for item in remote.active_internal_squads}:
                raise RemnawaveConfigurationError(
                    "Remnawave did not assign the configured Internal Squad"
                )
            self._save_remote(subscription, remote, squad_uuid)
            operation.status = ProvisioningOperationStatus.completed
            operation.completed_at = datetime.now(UTC)
            operation.last_error = None
            operation.next_retry_at = None
            add_audit_log(
                self.session,
                action="remnawave_user_created"
                if created
                else "remnawave_user_updated",
                entity_type="subscription",
                entity_id=subscription.id,
                actor_user_id=user.id,
                actor_telegram_id=user.telegram_id,
                details={"remnawave_user_uuid": str(remote.uuid)},
            )
            if not created and not had_uuid:
                add_audit_log(
                    self.session,
                    action="remnawave_user_linked",
                    entity_type="subscription",
                    entity_id=subscription.id,
                    actor_user_id=user.id,
                    actor_telegram_id=user.telegram_id,
                )
            if was_disabled:
                add_audit_log(
                    self.session,
                    action="remnawave_user_enabled",
                    entity_type="subscription",
                    entity_id=subscription.id,
                    actor_user_id=user.id,
                    actor_telegram_id=user.telegram_id,
                )
            await self.session.flush()
            return ProvisioningResult(
                status=SubscriptionStatus.active,
                external_user_uuid=str(remote.uuid),
                subscription_url_encrypted=subscription.subscription_url_encrypted,
            )
        except Exception as exc:
            return await self._fail(subscription, user, operation, exc)

    async def _resolve_or_create(
        self, subscription: Subscription, user: User
    ) -> tuple[RemnawaveUser, bool]:
        if subscription.remnawave_user_uuid:
            try:
                return await self.client.get_user(
                    subscription.remnawave_user_uuid,
                    operation="resolve_user_by_uuid",
                    local_user_id=user.id,
                    remnawave_username=subscription.remnawave_username,
                ), False
            except RemnawaveNotFoundError:
                pass
        assert subscription.remnawave_username is not None
        try:
            remote = await self.client.get_user_by_username(
                subscription.remnawave_username, local_user_id=user.id
            )
            self._validate_owner(remote, subscription, user)
            return remote, False
        except RemnawaveNotFoundError:
            pass

        request = CreateUserRequest(
            username=subscription.remnawave_username,
            status=RemnawaveUserStatus.active,
            trafficLimitStrategy=TrafficLimitStrategy.no_reset,
            expireAt=subscription.expires_at,
        )
        try:
            return await self.client.create_user(
                request, local_user_id=user.id
            ), True
        except (RemnawaveNetworkError, RemnawaveAPIError) as exc:
            if isinstance(exc, RemnawaveAPIError) and not (
                exc.retryable or isinstance(exc, RemnawaveConflictError)
            ):
                raise
            # The POST outcome can be uncertain. Resolve by the stable unique name;
            # never blindly repeat creation.
            try:
                remote = await self.client.get_user_by_username(
                    subscription.remnawave_username, local_user_id=user.id
                )
            except RemnawaveNotFoundError:
                raise exc from None
            self._validate_owner(remote, subscription, user)
            return remote, False

    async def _configure_remote(
        self,
        remote: RemnawaveUser,
        subscription: Subscription,
        user: User,
        squad_uuid: uuid.UUID,
        *,
        created: bool,
    ) -> RemnawaveUser:
        context = {
            "local_user_id": user.id,
            "remnawave_username": subscription.remnawave_username,
        }
        remote = await self.client.update_user(
            UpdateUserRequest(
                uuid=remote.uuid,
                activeInternalSquads=[squad_uuid],
            ),
            operation="assign_internal_squad",
            **context,
        )
        remote = await self.client.update_user(
            UpdateUserRequest(
                uuid=remote.uuid, hwidDeviceLimit=subscription.device_limit
            ),
            operation="set_device_limit",
            **context,
        )
        remote = await self.client.update_user(
            UpdateUserRequest(
                uuid=remote.uuid,
                trafficLimitBytes=self._traffic_limit_bytes(subscription),
                trafficLimitStrategy=TrafficLimitStrategy.no_reset,
            ),
            operation="set_traffic_limit",
            **context,
        )
        remote = await self.client.update_user(
            UpdateUserRequest(
                uuid=remote.uuid,
                status=RemnawaveUserStatus.active,
                telegramId=user.telegram_id,
                expireAt=None if created else subscription.expires_at,
            ),
            operation="set_user_identity",
            **context,
        )
        return await self.client.get_user(
            remote.uuid,
            operation="get_subscription_url",
            **context,
        )

    @staticmethod
    def _traffic_limit_bytes(subscription: Subscription) -> int:
        if subscription.is_unlimited_traffic or subscription.traffic_limit_gb is None:
            return 0
        return subscription.traffic_limit_gb * 1024**3

    @staticmethod
    def _validate_owner(
        remote: RemnawaveUser, subscription: Subscription, user: User
    ) -> None:
        if remote.username != subscription.remnawave_username:
            raise RemnawaveConflictError("Remnawave user ownership conflict")
        if remote.telegram_id is not None and remote.telegram_id != user.telegram_id:
            raise RemnawaveConflictError("Remnawave username belongs to another user")
        if (
            subscription.remnawave_user_uuid
            and str(remote.uuid) != subscription.remnawave_user_uuid
        ):
            raise RemnawaveConflictError("Remnawave UUID ownership conflict")

    @staticmethod
    def _save_remote_identity(
        subscription: Subscription, remote: RemnawaveUser
    ) -> None:
        subscription.remnawave_user_uuid = str(remote.uuid)
        subscription.external_user_uuid = str(remote.uuid)
        subscription.remnawave_short_uuid = remote.short_uuid
        subscription.remnawave_status = remote.status.value
        subscription.remnawave_created_at = remote.created_at

    def _save_remote(
        self, subscription: Subscription, remote: RemnawaveUser, squad_uuid: uuid.UUID
    ) -> None:
        now = datetime.now(UTC)
        subscription.remnawave_user_uuid = str(remote.uuid)
        subscription.external_user_uuid = str(remote.uuid)
        subscription.remnawave_short_uuid = remote.short_uuid
        subscription.subscription_url_encrypted = self.cipher.encrypt(
            remote.subscription_url
        )
        subscription.remnawave_status = remote.status.value
        subscription.remnawave_last_sync_at = now
        subscription.remnawave_sync_error = None
        subscription.remnawave_created_at = remote.created_at
        subscription.remnawave_internal_squad_uuid = str(squad_uuid)
        subscription.used_traffic_bytes = remote.user_traffic.used_traffic_bytes
        subscription.status = SubscriptionStatus.active
        subscription.provisioning_status = ProvisioningStatus.active
        subscription.last_activation_error = None
        subscription.next_retry_at = None

    async def _get_or_create_operation(
        self,
        subscription: Subscription,
        user: User,
        source: SubscriptionSource | str,
        order_id: uuid.UUID | None,
    ) -> ProvisioningOperation:
        source_value = (
            source.value if isinstance(source, SubscriptionSource) else source
        )
        if order_id:
            key = f"order:{order_id}"
        elif source_value == SubscriptionSource.admin.value:
            key = f"admin:{subscription.id}:{subscription.expires_at.isoformat()}"
        else:
            key = f"{source_value}:{subscription.id}"
        operation = await self.session.scalar(
            select(ProvisioningOperation)
            .where(ProvisioningOperation.idempotency_key == key)
            .with_for_update()
        )
        if operation is None:
            operation = ProvisioningOperation(
                user_id=user.id,
                subscription_id=subscription.id,
                order_id=order_id,
                idempotency_key=key,
                source=source_value,
                status=ProvisioningOperationStatus.pending,
                attempts=0,
            )
            self.session.add(operation)
            await self.session.flush()
        return operation

    async def _fail(
        self,
        subscription: Subscription,
        user: User,
        operation: ProvisioningOperation | None,
        exc: Exception,
    ) -> ProvisioningResult:
        if isinstance(exc, RemnawaveAPIError):
            body = exc.safe_response_body or "<empty>"
            error = (
                f"{exc}; operation={exc.operation}; retryable={exc.retryable}; "
                f"response={body}"
            )[:1000]
        else:
            error = str(exc)[:1000]
        attempts = (
            operation.attempts
            if operation is not None
            else subscription.activation_attempts + 1
        )
        delay = RETRY_DELAYS[min(max(attempts - 1, 0), len(RETRY_DELAYS) - 1)]
        next_retry = datetime.now(UTC) + timedelta(seconds=delay)
        subscription.status = SubscriptionStatus.activation_failed
        subscription.provisioning_status = ProvisioningStatus.failed
        subscription.last_activation_error = error
        subscription.remnawave_sync_error = error
        subscription.next_retry_at = next_retry
        if operation is not None:
            operation.status = ProvisioningOperationStatus.failed
            operation.last_error = error
            operation.next_retry_at = next_retry
        add_audit_log(
            self.session,
            action="remnawave_provisioning_failed",
            entity_type="subscription",
            entity_id=subscription.id,
            actor_user_id=user.id,
            actor_telegram_id=user.telegram_id,
            details={"error": error},
        )
        logger.warning(
            "Remnawave provisioning failed for local user %s: %s", user.id, error
        )
        await self.session.flush()
        return ProvisioningResult(
            status=SubscriptionStatus.activation_failed,
            external_user_uuid=subscription.remnawave_user_uuid,
            subscription_url_encrypted=subscription.subscription_url_encrypted,
        )


class RemnawaveSubscriptionAdapter:
    def __init__(self, service: RemnawaveProvisioningService) -> None:
        self.service = service

    async def provision(
        self, subscription: Subscription, user: User
    ) -> ProvisioningResult:
        return await self.service.provision(
            subscription,
            user,
            source=subscription.source_type,
            order_id=subscription.order_id,
        )

    async def order_was_applied(self, order_id: uuid.UUID) -> bool:
        operation = await self.service.session.scalar(
            select(ProvisioningOperation).where(
                ProvisioningOperation.order_id == order_id,
                ProvisioningOperation.status == ProvisioningOperationStatus.completed,
            )
        )
        return operation is not None
