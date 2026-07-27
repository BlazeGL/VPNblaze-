from datetime import UTC, datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.integrations.remnawave.enums import (
    RemnawaveUserStatus,
    TrafficLimitStrategy,
)


class RemnawaveModel(BaseModel):
    model_config = ConfigDict(extra="ignore", populate_by_name=True)


class InternalSquad(RemnawaveModel):
    uuid: UUID
    name: str


class InternalSquadResponse(RemnawaveModel):
    response: InternalSquad


class UserTraffic(RemnawaveModel):
    used_traffic_bytes: int = Field(alias="usedTrafficBytes")
    lifetime_used_traffic_bytes: int = Field(alias="lifetimeUsedTrafficBytes")
    online_at: datetime | None = Field(default=None, alias="onlineAt")
    first_connected_at: datetime | None = Field(default=None, alias="firstConnectedAt")
    last_connected_node_uuid: UUID | None = Field(
        default=None, alias="lastConnectedNodeUuid"
    )


class RemnawaveUser(RemnawaveModel):
    uuid: UUID
    id: int | None = None
    short_uuid: str = Field(alias="shortUuid")
    username: str
    status: RemnawaveUserStatus
    traffic_limit_bytes: int = Field(alias="trafficLimitBytes")
    traffic_limit_strategy: TrafficLimitStrategy = Field(alias="trafficLimitStrategy")
    expire_at: datetime = Field(alias="expireAt")
    telegram_id: int | None = Field(default=None, alias="telegramId")
    email: str | None = None
    description: str | None = None
    tag: str | None = None
    hwid_device_limit: int | None = Field(default=None, alias="hwidDeviceLimit")
    external_squad_uuid: UUID | None = Field(default=None, alias="externalSquadUuid")
    trojan_password: str | None = Field(default=None, alias="trojanPassword")
    vless_uuid: UUID | None = Field(default=None, alias="vlessUuid")
    ss_password: str | None = Field(default=None, alias="ssPassword")
    last_triggered_threshold: int | None = Field(
        default=None, alias="lastTriggeredThreshold"
    )
    sub_revoked_at: datetime | None = Field(default=None, alias="subRevokedAt")
    last_traffic_reset_at: datetime | None = Field(
        default=None, alias="lastTrafficResetAt"
    )
    created_at: datetime = Field(alias="createdAt")
    updated_at: datetime = Field(alias="updatedAt")
    subscription_url: str = Field(alias="subscriptionUrl")
    active_internal_squads: list[InternalSquad] = Field(
        default_factory=list, alias="activeInternalSquads"
    )
    user_traffic: UserTraffic = Field(alias="userTraffic")


class UserResponse(RemnawaveModel):
    response: RemnawaveUser


class UsersPage(RemnawaveModel):
    users: list[RemnawaveUser]
    total: int


class UsersPageResponse(RemnawaveModel):
    response: UsersPage


class DeleteResult(RemnawaveModel):
    is_deleted: bool = Field(alias="isDeleted")


class DeleteResponse(RemnawaveModel):
    response: DeleteResult


class CreateUserRequest(RemnawaveModel):
    username: str = Field(
        min_length=3, max_length=36, pattern=r"^[a-zA-Z0-9_-]+$"
    )
    status: RemnawaveUserStatus = RemnawaveUserStatus.active
    traffic_limit_bytes: int | None = Field(
        default=None, ge=0, alias="trafficLimitBytes"
    )
    traffic_limit_strategy: TrafficLimitStrategy = Field(
        default=TrafficLimitStrategy.no_reset, alias="trafficLimitStrategy"
    )
    expire_at: datetime = Field(alias="expireAt")
    telegram_id: int | None = Field(default=None, alias="telegramId")
    hwid_device_limit: int | None = Field(default=None, ge=0, alias="hwidDeviceLimit")
    active_internal_squads: list[UUID] | None = Field(
        default=None, alias="activeInternalSquads"
    )
    description: str | None = None
    tag: str | None = Field(
        default=None,
        max_length=16,
        pattern=r"^[A-Z0-9_]+$",
    )
    external_squad_uuid: UUID | None = Field(default=None, alias="externalSquadUuid")

    @field_validator("expire_at")
    @classmethod
    def expiration_must_be_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expireAt must be timezone-aware")
        return value.astimezone(UTC)


class UpdateUserRequest(RemnawaveModel):
    uuid: UUID
    status: RemnawaveUserStatus | None = None
    telegram_id: int | None = Field(default=None, alias="telegramId")
    traffic_limit_bytes: int | None = Field(
        default=None, ge=0, alias="trafficLimitBytes"
    )
    traffic_limit_strategy: TrafficLimitStrategy | None = Field(
        default=None, alias="trafficLimitStrategy"
    )
    expire_at: datetime | None = Field(default=None, alias="expireAt")
    hwid_device_limit: int | None = Field(default=None, ge=0, alias="hwidDeviceLimit")
    active_internal_squads: list[UUID] | None = Field(
        default=None, alias="activeInternalSquads"
    )
    description: str | None = None
    tag: str | None = Field(
        default=None,
        max_length=16,
        pattern=r"^[A-Z0-9_]+$",
    )
    email: str | None = None
    external_squad_uuid: UUID | None = Field(default=None, alias="externalSquadUuid")

    @field_validator("expire_at")
    @classmethod
    def expiration_must_be_utc(cls, value: datetime | None) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("expireAt must be timezone-aware")
        return value.astimezone(UTC)
