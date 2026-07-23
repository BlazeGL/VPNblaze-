from app.core.config import Settings


def test_admin_ids_are_parsed_from_csv() -> None:
    settings = Settings(
        telegram_bot_token="test-token",
        postgres_password="secret",
        admin_ids="123456789, 987654321",
        _env_file=None,
    )

    assert settings.admin_ids == [123456789, 987654321]


def test_database_and_redis_urls() -> None:
    settings = Settings(
        telegram_bot_token="test-token",
        postgres_password="secret",
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
        telegram_bot_token="test-token",
        postgres_password="secret",
        yookassa_shop_id="1234567",
        yookassa_secret_key="live-key",
        _env_file=None,
    )

    assert settings.yookassa_missing_settings == []
    assert settings.yookassa_api_url == "https://api.yookassa.ru/v3"


def test_user_agreement_url_accepts_only_https_without_spaces() -> None:
    valid = Settings(
        telegram_bot_token="test-token",
        postgres_password="secret",
        user_agreement_url="https://legal.example.org/agreement",
        _env_file=None,
    )
    invalid = Settings(
        telegram_bot_token="test-token",
        postgres_password="secret",
        user_agreement_url="http://legal.example.org/agreement",
        _env_file=None,
    )

    assert valid.user_agreement_url == "https://legal.example.org/agreement"
    assert invalid.user_agreement_url is None
