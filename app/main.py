import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.client.default import DefaultBotProperties
from aiogram.fsm.storage.redis import RedisStorage
from redis.asyncio import Redis

from app.bot.handlers import setup_routers
from app.bot.services.command_menu import register_command_menu
from app.bot.services.support import validate_support_group
from app.core.config import get_settings
from app.core.crypto import SubscriptionUrlCipher
from app.core.logging import configure_logging
from app.database.session import create_engine_and_session
from app.integrations.remnawave.client import RemnawaveClient
from app.integrations.remnawave.exceptions import RemnawaveError
from app.integrations.yookassa.client import YooKassaClient
from app.integrations.yookassa.exceptions import YooKassaError
from app.workers.subscription_retry import retry_subscription_activations

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, session_factory = create_engine_and_session(settings.database_url)
    storage = RedisStorage.from_url(settings.redis_url)
    redis_client = Redis.from_url(settings.redis_url, decode_responses=True)
    bot = Bot(
        token=settings.telegram_bot_token,
        default=DefaultBotProperties(link_preview_is_disabled=True),
    )
    await validate_support_group(bot, settings.support_group_id)
    payment_return_url = settings.yookassa_return_url
    if not payment_return_url:
        try:
            identity = await bot.get_me()
            if identity.username:
                payment_return_url = f"https://t.me/{identity.username}"
        except Exception:
            logger.exception(
                "Could not resolve the bot username for payment return URL"
            )
    payment_return_url = payment_return_url or "https://t.me"
    payment_client = YooKassaClient(
        settings.yookassa_shop_id,
        settings.yookassa_secret_key,
        base_url=settings.yookassa_api_url,
        default_return_url=payment_return_url,
        timeout=settings.yookassa_request_timeout,
    )
    if settings.yookassa_missing_settings:
        logger.error(
            "YooKassa payments are disabled; missing settings: %s",
            ", ".join(settings.yookassa_missing_settings),
        )
    else:
        try:
            await payment_client.check_api()
            logger.info("YooKassa API authentication and availability check succeeded")
        except YooKassaError as exc:
            logger.error("YooKassa API check failed; bot will continue: %s", exc)
    remnawave_client: RemnawaveClient | None = None
    subscription_cipher: SubscriptionUrlCipher | None = None
    logger.info(
        "Remnawave configuration:\n"
        "- base_url: %s\n"
        "- api_token: %s\n"
        "- internal_squad_uuid: %s\n"
        "- russia_squad_uuid: %s\n"
        "- template_user_uuid: %s\n"
        "- encryption_key: %s",
        "configured" if settings.remnawave_base_url else "missing",
        "configured" if settings.remnawave_api_token else "missing",
        "configured" if settings.remnawave_internal_squad_uuid else "missing",
        "configured" if settings.remnawave_russia_squad_uuid else "missing",
        "configured" if settings.remnawave_template_user_uuid else "not configured",
        "configured" if settings.subscription_encryption_key else "missing",
    )
    if settings.remnawave_missing_settings:
        logger.error(
            "Remnawave provisioning is disabled; missing settings: %s",
            ", ".join(settings.remnawave_missing_settings),
        )
    else:
        try:
            subscription_cipher = SubscriptionUrlCipher(
                settings.subscription_encryption_key or ""
            )
            remnawave_client = RemnawaveClient(
                settings.remnawave_base_url or "",
                settings.remnawave_api_token or "",
                timeout=settings.remnawave_request_timeout,
                verify_ssl=settings.remnawave_verify_ssl,
                max_retries=settings.remnawave_max_retries,
                retry_base_delay=settings.remnawave_retry_base_delay,
            )
            await remnawave_client.check_api()
            logger.info("Remnawave API authentication and availability check succeeded")
            await remnawave_client.get_internal_squad(
                settings.remnawave_internal_squad_uuid or ""
            )
            if settings.remnawave_russia_squad_uuid:
                await remnawave_client.get_internal_squad(
                    settings.remnawave_russia_squad_uuid
                )
            if settings.remnawave_template_user_uuid:
                await remnawave_client.get_user(
                    settings.remnawave_template_user_uuid,
                    operation="validate_new_user_template",
                )
            logger.info("Remnawave provisioning sources validation succeeded")
        except (RemnawaveError, ValueError) as exc:
            logger.error("Remnawave API is unavailable; bot will continue: %s", exc)
    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(setup_routers())
    try:
        await register_command_menu(bot, set(settings.admin_ids))
        logger.info("Telegram command menus registered")
    except Exception:
        logger.exception("Could not register Telegram command menus")
    stop_retry_worker = asyncio.Event()
    retry_worker = asyncio.create_task(
        retry_subscription_activations(
            session_factory=session_factory,
            bot=bot,
            admin_ids=set(settings.admin_ids),
            stop_event=stop_retry_worker,
            remnawave_client=remnawave_client,
            subscription_cipher=subscription_cipher,
            internal_squad_uuid=settings.remnawave_internal_squad_uuid,
            russia_squad_uuid=settings.remnawave_russia_squad_uuid,
            template_user_uuid=settings.remnawave_template_user_uuid,
        )
    )
    logger.info("Starting VPN bot in long polling mode")
    try:
        await dispatcher.start_polling(
            bot,
            session_factory=session_factory,
            admin_ids=set(settings.admin_ids),
            payment_client=payment_client,
            payment_return_url=payment_return_url,
            public_base_url=settings.public_base_url,
            redis_client=redis_client,
            remnawave_client=remnawave_client,
            subscription_cipher=subscription_cipher,
            remnawave_internal_squad_uuid=settings.remnawave_internal_squad_uuid,
            remnawave_russia_squad_uuid=settings.remnawave_russia_squad_uuid,
            remnawave_template_user_uuid=settings.remnawave_template_user_uuid,
            settings=settings,
        )
    except Exception:
        logger.exception("Bot stopped because of an unexpected error")
        raise
    finally:
        logger.info("Shutting down bot resources")
        stop_retry_worker.set()
        await retry_worker
        await bot.session.close()
        await storage.close()
        await redis_client.aclose()
        await payment_client.aclose()
        if remnawave_client is not None:
            await remnawave_client.aclose()
        await engine.dispose()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
