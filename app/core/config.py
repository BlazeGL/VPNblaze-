from functools import lru_cache
from typing import Annotated
from urllib.parse import urlsplit
from uuid import UUID

from pydantic import Field, ValidationInfo, field_validator
from pydantic_settings import BaseSettings, NoDecode, SettingsConfigDict

from app.integrations.remnawave.enums import TrafficLimitStrategy

DEFAULT_YOOKASSA_API_URL = "https://api.yookassa.ru/v3"
DEFAULT_YOOKASSA_REQUEST_TIMEOUT = 15.0
DEFAULT_REMNAWAVE_REQUEST_TIMEOUT = 15.0
DEFAULT_REMNAWAVE_VERIFY_SSL = True
DEFAULT_REMNAWAVE_MAX_RETRIES = 3
DEFAULT_REMNAWAVE_RETRY_BASE_DELAY = 1.0
REMNAWAVE_NEW_USER_TRAFFIC_LIMIT_BYTES = 600 * 1024**3
REMNAWAVE_NEW_USER_TRAFFIC_LIMIT_STRATEGY = TrafficLimitStrategy.month
REMNAWAVE_NEW_USER_HWID_DEVICE_LIMIT = 5

EMPTY_VALUE_DEFAULTS: dict[str, object] = {
    "postgres_db": "vpn_bot",
    "postgres_user": "vpn_bot",
    "postgres_host": "postgres",
    "postgres_port": 5432,
    "redis_host": "redis",
    "redis_port": 6379,
    "log_level": "INFO",
    "yookassa_request_timeout": DEFAULT_YOOKASSA_REQUEST_TIMEOUT,
    "remnawave_request_timeout": DEFAULT_REMNAWAVE_REQUEST_TIMEOUT,
    "remnawave_verify_ssl": DEFAULT_REMNAWAVE_VERIFY_SSL,
    "remnawave_max_retries": DEFAULT_REMNAWAVE_MAX_RETRIES,
    "remnawave_retry_base_delay": DEFAULT_REMNAWAVE_RETRY_BASE_DELAY,
}


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    telegram_bot_token: str = Field(min_length=1)
    admin_ids: Annotated[list[int], NoDecode] = Field(default_factory=list)
    postgres_db: str = "vpn_bot"
    postgres_user: str = "vpn_bot"
    postgres_password: str = Field(min_length=1)
    postgres_host: str = "postgres"
    postgres_port: int = 5432
    redis_host: str = "redis"
    redis_port: int = 6379
    log_level: str = "INFO"
    onlipay_api_url: str | None = None
    onlipay_api_key: str | None = None
    onlipay_secret_key: str | None = None
    onlipay_merchant_id: str | None = None
    onlipay_webhook_secret: str | None = None
    public_base_url: str | None = None
    yookassa_shop_id: str | None = None
    yookassa_secret_key: str | None = None
    yookassa_api_url: str = DEFAULT_YOOKASSA_API_URL
    yookassa_return_url: str | None = None
    yookassa_request_timeout: float = Field(
        default=DEFAULT_YOOKASSA_REQUEST_TIMEOUT,
        gt=0,
    )
    remnawave_base_url: str | None = None
    remnawave_api_token: str = Field(min_length=1)
    remnawave_internal_squad_uuid: str | None = None
    remnawave_russia_squad_uuid: str | None = None
    remnawave_template_user_uuid: str | None = None
    remnawave_subscription_base_url: str | None = None
    remnawave_request_timeout: float = Field(
        default=DEFAULT_REMNAWAVE_REQUEST_TIMEOUT,
        gt=0,
    )
    remnawave_verify_ssl: bool = DEFAULT_REMNAWAVE_VERIFY_SSL
    remnawave_max_retries: int = Field(
        default=DEFAULT_REMNAWAVE_MAX_RETRIES,
        ge=0,
        le=10,
    )
    remnawave_retry_base_delay: float = Field(
        default=DEFAULT_REMNAWAVE_RETRY_BASE_DELAY,
        ge=0,
    )
    subscription_encryption_key: str | None = None
    android_app_url: str | None = None
    ios_app_url: str | None = None
    windows_app_url: str | None = None
    linux_app_url: str | None = None
    user_agreement_url: str | None = None
    support_url: str = "https://t.me/Blaze_GL"
    support_group_id: int | None = None

    @field_validator("admin_ids", mode="before")
    @classmethod
    def parse_admin_ids(cls, value: object) -> object:
        if isinstance(value, str):
            return [int(item.strip()) for item in value.split(",") if item.strip()]
        return value

    @field_validator(*EMPTY_VALUE_DEFAULTS, mode="before")
    @classmethod
    def empty_string_to_default(
        cls,
        value: object,
        info: ValidationInfo,
    ) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return EMPTY_VALUE_DEFAULTS[info.field_name]
        return value

    @field_validator(
        "onlipay_api_url",
        "onlipay_api_key",
        "onlipay_secret_key",
        "onlipay_merchant_id",
        "onlipay_webhook_secret",
        "public_base_url",
        "yookassa_shop_id",
        "yookassa_secret_key",
        "yookassa_return_url",
        "subscription_encryption_key",
        "android_app_url",
        "ios_app_url",
        "windows_app_url",
        "linux_app_url",
        mode="before",
    )
    @classmethod
    def empty_string_to_none(cls, value: object) -> object:
        return None if value == "" else value

    @field_validator("user_agreement_url", mode="before")
    @classmethod
    def validate_user_agreement_url(cls, value: object) -> str | None:
        if value is None:
            return None
        candidate = str(value)
        parsed = urlsplit(candidate)
        if (
            not candidate
            or any(character.isspace() for character in candidate)
            or parsed.scheme != "https"
            or not parsed.netloc
        ):
            return None
        return candidate

    @field_validator("support_url", mode="before")
    @classmethod
    def validate_support_url(cls, value: object) -> str:
        candidate = str(value or "https://t.me/Blaze_GL").strip()
        parsed = urlsplit(candidate)
        if (
            not candidate
            or any(character.isspace() for character in candidate)
            or parsed.scheme != "https"
            or not parsed.netloc
        ):
            raise ValueError("SUPPORT_URL must be an HTTPS URL")
        return candidate

    @field_validator("support_group_id", mode="before")
    @classmethod
    def validate_support_group_id(cls, value: object) -> int | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            candidate = int(value)
        except (TypeError, ValueError) as exc:
            raise ValueError("SUPPORT_GROUP_ID must be a numeric chat ID") from exc
        if candidate >= 0:
            raise ValueError("SUPPORT_GROUP_ID must be a negative supergroup ID")
        return candidate

    @field_validator("yookassa_api_url", mode="before")
    @classmethod
    def validate_yookassa_api_url(cls, value: object) -> str:
        if value is None or (isinstance(value, str) and not value.strip()):
            return DEFAULT_YOOKASSA_API_URL
        candidate = str(value).strip().rstrip("/")
        parsed = urlsplit(candidate)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("YooKassa URLs must use HTTPS")
        return candidate

    @field_validator("yookassa_return_url", mode="before")
    @classmethod
    def validate_yookassa_return_url(cls, value: object) -> str | None:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        candidate = str(value).strip().rstrip("/")
        parsed = urlsplit(candidate)
        if parsed.scheme != "https" or not parsed.netloc:
            raise ValueError("YooKassa URLs must use HTTPS")
        return candidate

    @field_validator(
        "remnawave_base_url",
        "remnawave_subscription_base_url",
        mode="before",
    )
    @classmethod
    def validate_remnawave_base_url(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        candidate = str(value).strip().rstrip("/")
        parsed = urlsplit(candidate)
        if parsed.scheme not in {"http", "https"} or not parsed.netloc:
            raise ValueError("Remnawave URLs must be HTTP(S) URLs")
        return candidate

    @field_validator("remnawave_internal_squad_uuid", mode="before")
    @classmethod
    def validate_internal_squad_uuid(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return str(UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError("REMNAWAVE_INTERNAL_SQUAD_UUID must be a UUID") from exc

    @field_validator("remnawave_russia_squad_uuid", mode="before")
    @classmethod
    def validate_russia_squad_uuid(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return str(UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError("REMNAWAVE_RUSSIA_SQUAD_UUID must be a UUID") from exc

    @field_validator("remnawave_template_user_uuid", mode="before")
    @classmethod
    def validate_template_user_uuid(cls, value: object) -> object:
        if value is None or (isinstance(value, str) and not value.strip()):
            return None
        try:
            return str(UUID(str(value).strip()))
        except ValueError as exc:
            raise ValueError("REMNAWAVE_TEMPLATE_USER_UUID must be a UUID") from exc

    @property
    def database_url(self) -> str:
        return (
            f"postgresql+asyncpg://{self.postgres_user}:{self.postgres_password}"
            f"@{self.postgres_host}:{self.postgres_port}/{self.postgres_db}"
        )

    @property
    def redis_url(self) -> str:
        return f"redis://{self.redis_host}:{self.redis_port}/0"

    @property
    def remnawave_missing_settings(self) -> list[str]:
        values = {
            "REMNAWAVE_BASE_URL": self.remnawave_base_url,
            "REMNAWAVE_API_TOKEN": self.remnawave_api_token,
            "REMNAWAVE_INTERNAL_SQUAD_UUID": self.remnawave_internal_squad_uuid,
            "SUBSCRIPTION_ENCRYPTION_KEY": self.subscription_encryption_key,
        }
        if not self.remnawave_template_user_uuid:
            values["REMNAWAVE_RUSSIA_SQUAD_UUID"] = self.remnawave_russia_squad_uuid
        return [name for name, value in values.items() if not value]

    @property
    def yookassa_missing_settings(self) -> list[str]:
        values = {
            "YOOKASSA_SHOP_ID": self.yookassa_shop_id,
            "YOOKASSA_SECRET_KEY": self.yookassa_secret_key,
        }
        return [name for name, value in values.items() if not value]


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]
