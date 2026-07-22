import logging

from aiogram import F, Router
from aiogram.filters import CommandStart
from aiogram.types import CallbackQuery, Message
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards import build_main_menu
from app.database.repositories import UserRepository

logger = logging.getLogger(__name__)
router = Router(name=__name__)
MENU_CALLBACKS = {"how_to_connect", "support"}


@router.message(CommandStart())
async def handle_start(
    message: Message,
    session_factory: async_sessionmaker[AsyncSession],
    admin_ids: set[int],
) -> None:
    telegram_user = message.from_user
    if telegram_user is None:
        return
    async with session_factory() as session, session.begin():
        repository = UserRepository(session)
        user, _ = await repository.get_or_create(
            telegram_id=telegram_user.id,
            username=telegram_user.username,
            first_name=telegram_user.first_name,
            last_name=telegram_user.last_name,
            is_admin=telegram_user.id in admin_ids,
        )
        user.is_admin = telegram_user.id in admin_ids
    logger.info("Processed /start for Telegram user %s", telegram_user.id)
    await message.answer(
        "Добро пожаловать! Выберите нужный раздел:",
        reply_markup=build_main_menu(),
    )


@router.callback_query(F.data.in_(MENU_CALLBACKS))
async def handle_menu_placeholder(callback: CallbackQuery) -> None:
    await callback.answer()
    if callback.message is not None:
        await callback.message.answer("Раздел находится в разработке")
