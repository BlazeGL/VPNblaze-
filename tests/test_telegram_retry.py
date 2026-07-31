from unittest.mock import AsyncMock

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import GetMe

from app.bot.services.telegram_retry import TelegramNetworkRetryMiddleware


def network_error() -> TelegramNetworkError:
    return TelegramNetworkError(method=GetMe(), message="temporary timeout")


@pytest.mark.asyncio
async def test_telegram_network_retry_succeeds_after_transient_failures(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    make_request = AsyncMock(
        side_effect=[network_error(), network_error(), "ok"]
    )
    sleep = AsyncMock()
    monkeypatch.setattr("app.bot.services.telegram_retry.asyncio.sleep", sleep)
    middleware = TelegramNetworkRetryMiddleware(max_attempts=3, base_delay=0.5)

    result = await middleware(make_request, object(), GetMe())  # type: ignore[arg-type]

    assert result == "ok"
    assert make_request.await_count == 3
    assert [call.args[0] for call in sleep.await_args_list] == [0.5, 1.0]


@pytest.mark.asyncio
async def test_telegram_network_retry_raises_after_last_attempt() -> None:
    make_request = AsyncMock(side_effect=network_error())
    middleware = TelegramNetworkRetryMiddleware(max_attempts=2, base_delay=0)

    with pytest.raises(TelegramNetworkError):
        await middleware(make_request, object(), GetMe())  # type: ignore[arg-type]

    assert make_request.await_count == 2
