import asyncio
import logging

from aiogram import Bot, Dispatcher
from aiogram.fsm.storage.redis import RedisStorage

from app.bot.handlers import setup_routers
from app.core.config import get_settings
from app.core.logging import configure_logging
from app.database.session import create_engine_and_session

logger = logging.getLogger(__name__)


async def main() -> None:
    settings = get_settings()
    configure_logging(settings.log_level)
    engine, session_factory = create_engine_and_session(settings.database_url)
    storage = RedisStorage.from_url(settings.redis_url)
    bot = Bot(token=settings.telegram_bot_token)
    dispatcher = Dispatcher(storage=storage)
    dispatcher.include_router(setup_routers())
    logger.info("Starting VPN bot in long polling mode")
    try:
        await dispatcher.start_polling(
            bot,
            session_factory=session_factory,
            admin_ids=set(settings.admin_ids),
        )
    except Exception:
        logger.exception("Bot stopped because of an unexpected error")
        raise
    finally:
        logger.info("Shutting down bot resources")
        await bot.session.close()
        await storage.close()
        await engine.dispose()
        logger.info("Bot stopped")


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        pass
