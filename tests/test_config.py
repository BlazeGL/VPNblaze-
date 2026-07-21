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
