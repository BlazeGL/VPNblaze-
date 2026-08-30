from typing import cast

from aiogram import Bot
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import TelegramMethod
from aiogram.methods.base import TelegramType
from aiohttp import ClientError, ClientTimeout

TELEGRAM_CONNECT_TIMEOUT_SECONDS = 3.0


def telegram_http_timeout(total: float) -> ClientTimeout:
    """Keep uploads intact while failing a stalled TCP connection quickly."""
    connect_timeout = min(total, TELEGRAM_CONNECT_TIMEOUT_SECONDS)
    return ClientTimeout(
        total=total,
        connect=connect_timeout,
        sock_connect=connect_timeout,
    )


class ResilientTelegramSession(AiohttpSession):
    async def make_request(
        self,
        bot: Bot,
        method: TelegramMethod[TelegramType],
        timeout: int | None = None,  # noqa: ASYNC109 - parent API compatibility
    ) -> TelegramType:
        session = await self.create_session()
        url = self.api.api_url(token=bot.token, method=method.__api_method__)
        form = self.build_form_data(bot=bot, method=method)
        total_timeout = float(self.timeout if timeout is None else timeout)

        try:
            async with session.post(
                url,
                data=form,
                timeout=telegram_http_timeout(total_timeout),
            ) as response:
                raw_result = await response.text()
        except TimeoutError as exc:
            raise TelegramNetworkError(
                method=method,
                message="Request timeout error",
            ) from exc
        except ClientError as exc:
            raise TelegramNetworkError(
                method=method,
                message=f"{type(exc).__name__}: {exc}",
            ) from exc

        result = self.check_response(
            bot=bot,
            method=method,
            status_code=response.status,
            content=raw_result,
        )
        return cast(TelegramType, result.result)
