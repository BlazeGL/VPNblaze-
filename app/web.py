import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from aiogram import Bot
from aiogram.client.default import DefaultBotProperties
from fastapi import FastAPI

from app.core.config import get_settings
from app.core.crypto import SubscriptionUrlCipher
from app.core.logging import configure_logging
from app.database.session import create_engine_and_session
from app.integrations.onlipay.client import OnliPayClient
from app.integrations.onlipay.signature import UnavailableWebhookVerifier
from app.integrations.remnawave.client import RemnawaveClient
from app.webhooks.onlipay import router as onlipay_webhook_router

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, session_factory = create_engine_and_session(settings.database_url)
    bot = Bot(
        settings.telegram_bot_token,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    app.state.settings = settings
    app.state.session_factory = session_factory
    app.state.onlipay_client = OnliPayClient()
    app.state.onlipay_webhook_verifier = UnavailableWebhookVerifier()
    app.state.remnawave_client = None
    app.state.subscription_cipher = None
    if not settings.remnawave_missing_settings:
        try:
            app.state.subscription_cipher = SubscriptionUrlCipher(
                settings.subscription_encryption_key or ""
            )
            app.state.remnawave_client = RemnawaveClient(
                settings.remnawave_base_url or "",
                settings.remnawave_api_token or "",
                timeout=settings.remnawave_request_timeout,
                verify_ssl=settings.remnawave_verify_ssl,
                max_retries=settings.remnawave_max_retries,
                retry_base_delay=settings.remnawave_retry_base_delay,
            )
            await app.state.remnawave_client.check_api()
            await app.state.remnawave_client.get_internal_squad(
                settings.remnawave_internal_squad_uuid or ""
            )
        except Exception as exc:
            logger.error("Remnawave API is unavailable; webhook stays up: %s", exc)
    app.state.bot = bot
    try:
        yield
    finally:
        await bot.session.close()
        if app.state.remnawave_client is not None:
            await app.state.remnawave_client.aclose()
        await engine.dispose()


app = FastAPI(title="VPN bot webhooks", lifespan=lifespan)
app.include_router(onlipay_webhook_router)
