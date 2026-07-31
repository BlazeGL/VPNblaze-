import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

from aiogram import Bot
from aiogram.exceptions import TelegramNetworkError

logger = logging.getLogger(__name__)

TELEGRAM_REQUEST_TIMEOUT_SECONDS = 10.0
TELEGRAM_REQUEST_MAX_ATTEMPTS = 3
TELEGRAM_REQUEST_RETRY_BASE_DELAY_SECONDS = 0.5


class TelegramNetworkRetryMiddleware:
    """Retry transient Bot API network failures with a short backoff."""

    def __init__(
        self,
        *,
        max_attempts: int = TELEGRAM_REQUEST_MAX_ATTEMPTS,
        base_delay: float = TELEGRAM_REQUEST_RETRY_BASE_DELAY_SECONDS,
    ) -> None:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if base_delay < 0:
            raise ValueError("base_delay must be non-negative")
        self.max_attempts = max_attempts
        self.base_delay = base_delay

    async def __call__(
        self,
        make_request: Callable[[Bot, Any], Awaitable[Any]],
        bot: Bot,
        method: Any,
    ) -> Any:
        method_name = getattr(method, "__api_method__", type(method).__name__)
        for attempt in range(1, self.max_attempts + 1):
            try:
                return await make_request(bot, method)
            except TelegramNetworkError:
                if attempt >= self.max_attempts:
                    raise
                delay = self.base_delay * attempt
                logger.warning(
                    "Telegram API network failure for %s; retrying (%s/%s) in %.1fs",
                    method_name,
                    attempt + 1,
                    self.max_attempts,
                    delay,
                )
                await asyncio.sleep(delay)

        raise RuntimeError("Telegram retry loop ended unexpectedly")
