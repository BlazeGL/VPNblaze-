import pytest
from pydantic import ValidationError

from app.core.config import Settings

REQUIRED_SETTINGS = {
    "telegram_bot_token": "test-token",
    "postgres_password": "secret",
    "remnawave_api_token": "remnawave-token",
}


def test_admin_ids_are_parsed_from_csv() -> None:
    settings = Settings(
        **REQUIRED_SETTINGS,
        admin_ids="123456789, 987654321",
        _env_file=None,
    )

    assert settings.admin_ids == [123456789, 987654321]


def test_database_and_redis_urls() -> None:
    settings = Settings(
        **REQUIRED_SETTINGS,
        postgres_host="db",
        redis_host="cache",
        _env_file=None,
    )

    assert settings.database_url == (
        "postgresql+asyncpg://vpn_bot:secret@db:5432/vpn_bot"
    )
    assert settings.redis_url == "redis://cache:6379/0"


def test_yookassa_credentials_are_loaded() -> None:
    settings = Settings(
        **REQUIRED_SETTINGS,
        yookassa_shop_id="1234567",
        yookassa_secret_key="live-key",
        _env_file=None,
    )

    assert settings.yookassa_missing_settings == []
    assert settings.yookassa_api_url == "https://api.yookassa.ru/v3"


def test_user_agreement_url_accepts_only_https_without_spaces() -> None:
    valid = Settings(
        **REQUIRED_SETTINGS,
        user_agreement_url="https://legal.example.org/agreement",
        _env_file=None,
    )
    invalid = Settings(
        **REQUIRED_SETTINGS,
        user_agreement_url="http://legal.example.org/agreement",
        _env_file=None,
    )

    assert valid.user_agreement_url == "https://legal.example.org/agreement"
    assert invalid.user_agreement_url is None


def test_support_group_id_accepts_only_numeric_supergroup_ids() -> None:
    configured = Settings(
        **REQUIRED_SETTINGS,
        support_group_id="-1001234567890",
        _env_file=None,
    )
    empty = Settings(
        **REQUIRED_SETTINGS,
        support_group_id="",
        _env_file=None,
    )

    assert configured.support_group_id == -1001234567890
    assert empty.support_group_id is None

    for invalid in ("not-a-chat", "123456789"):
        with pytest.raises(ValidationError):
            Settings(
                **REQUIRED_SETTINGS,
                support_group_id=invalid,
                _env_file=None,
            )


def test_empty_technical_settings_use_safe_defaults() -> None:
    settings = Settings(
        **REQUIRED_SETTINGS,
        postgres_db="",
        postgres_user="",
        postgres_host="",
        postgres_port="",
        redis_host="",
        redis_port="",
        log_level="",
        yookassa_api_url="",
        yookassa_request_timeout="",
        remnawave_request_timeout="",
        remnawave_verify_ssl="",
        remnawave_max_retries="",
        remnawave_retry_base_delay="",
        _env_file=None,
    )

    assert settings.postgres_db == "vpn_bot"
    assert settings.postgres_user == "vpn_bot"
    assert settings.postgres_host == "postgres"
    assert settings.postgres_port == 5432
    assert settings.redis_host == "redis"
    assert settings.redis_port == 6379
    assert settings.log_level == "INFO"
    assert settings.yookassa_api_url == "https://api.yookassa.ru/v3"
    assert settings.yookassa_request_timeout == 15
    assert settings.remnawave_request_timeout == 15
    assert settings.remnawave_verify_ssl is True
    assert settings.remnawave_max_retries == 3
    assert settings.remnawave_retry_base_delay == 1


def test_critical_secrets_remain_required() -> None:
    with pytest.raises(ValidationError):
        Settings(
            telegram_bot_token="",
            postgres_password="",
            remnawave_api_token="",
            _env_file=None,
        )
