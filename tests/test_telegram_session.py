from app.bot.services.telegram_session import (
    TELEGRAM_CONNECT_TIMEOUT_SECONDS,
    telegram_http_timeout,
)


def test_telegram_timeout_fails_connect_quickly_but_keeps_total_timeout() -> None:
    timeout = telegram_http_timeout(20.0)

    assert timeout.total == 20.0
    assert timeout.connect == TELEGRAM_CONNECT_TIMEOUT_SECONDS
    assert timeout.sock_connect == TELEGRAM_CONNECT_TIMEOUT_SECONDS


def test_telegram_connect_timeout_never_exceeds_short_total_timeout() -> None:
    timeout = telegram_http_timeout(2.0)

    assert timeout.total == 2.0
    assert timeout.connect == 2.0
    assert timeout.sock_connect == 2.0
