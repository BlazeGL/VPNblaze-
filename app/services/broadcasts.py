import asyncio
import logging
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import (
    TelegramAPIError,
    TelegramNetworkError,
    TelegramRetryAfter,
    TelegramServerError,
)

logger = logging.getLogger(__name__)
Sleep = Callable[[float], Awaitable[None]]


@dataclass(frozen=True, slots=True)
class BroadcastResult:
    successful: int
    errors: int

    @property
    def total(self) -> int:
        return self.successful + self.errors


async def copy_broadcast_to_users(
    bot: Bot,
    telegram_ids: Iterable[int],
    *,
    from_chat_id: int,
    message_id: int,
    delay_seconds: float = 0.05,
    max_attempts: int = 3,
    sleep: Sleep = asyncio.sleep,
) -> BroadcastResult:
    """Copy one admin message to every recipient without aborting the batch."""
    successful = 0
    errors = 0

    for telegram_id in telegram_ids:
        delivered = False
        for attempt in range(max_attempts):
            try:
                await bot.copy_message(
                    chat_id=telegram_id,
                    from_chat_id=from_chat_id,
                    message_id=message_id,
                )
                delivered = True
                break
            except TelegramRetryAfter as exc:
                if attempt + 1 >= max_attempts:
                    break
                await sleep(float(exc.retry_after) + 0.1)
            except (TelegramNetworkError, TelegramServerError):
                if attempt + 1 >= max_attempts:
                    break
                await sleep(float(2**attempt))
            except TelegramAPIError as exc:
                logger.warning(
                    "Broadcast delivery rejected for Telegram user %s: %s",
                    telegram_id,
                    exc,
                )
                break
            except Exception:
                logger.exception(
                    "Unexpected broadcast delivery failure for Telegram user %s",
                    telegram_id,
                )
                break

        if delivered:
            successful += 1
        else:
            errors += 1
        if delay_seconds > 0:
            await sleep(delay_seconds)

    return BroadcastResult(successful=successful, errors=errors)
