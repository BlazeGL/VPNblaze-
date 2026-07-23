from decimal import Decimal

from aiogram import F, Router
from aiogram.enums import ParseMode
from aiogram.types import CallbackQuery
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.bot.keyboards.start import (
    BONUSES_CALLBACK,
    COPY_REFERRAL_LINK_CALLBACK,
    bonuses_menu,
)
from app.bot.rendering import edit_text_or_caption
from app.database.repositories import UserRepository
from app.services.referrals import ReferralService

router = Router(name=__name__)


def money(value: Decimal) -> str:
    rendered = f"{value:.2f}".rstrip("0").rstrip(".")
    return rendered.replace(".", ",")


async def _referral_link(callback: CallbackQuery, code: str) -> str:
    identity = await callback.bot.get_me()
    if not identity.username:
        raise RuntimeError("Bot username is unavailable")
    return ReferralService.deep_link(identity.username, code)


@router.callback_query(F.data == BONUSES_CALLBACK)
async def show_bonuses(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(
            callback.from_user.id
        )
    if user is None:
        await callback.answer("Сначала нажмите /start.", show_alert=True)
        return
    try:
        link = await _referral_link(callback, user.referral_code)
    except RuntimeError:
        await callback.answer(
            "Ссылка временно недоступна. Попробуйте позже.", show_alert=True
        )
        return
    text = (
        "👥 <b>Пригласите друзей</b>\n\n"
        "За каждого нового пользователя, выполнившего условия действующей "
        "реферальной программы, вы получаете <b>50 ₽</b> на баланс.\n\n"
        "👥 Приглашено пользователей\n"
        f"<b>{user.total_referrals}</b>\n\n"
        "💵 Начислено бонусов\n"
        f"<b>{money(user.total_referral_income)} ₽</b>\n\n"
        "🔗 Ваша реферальная ссылка\n"
        f"<code>{link}</code>"
    )
    await callback.answer()
    if callback.message:
        await edit_text_or_caption(
            callback.message,
            text,
            bonuses_menu(link),
            parse_mode=ParseMode.HTML,
        )


@router.callback_query(F.data == COPY_REFERRAL_LINK_CALLBACK)
async def copy_referral_link(
    callback: CallbackQuery,
    session_factory: async_sessionmaker[AsyncSession],
) -> None:
    async with session_factory() as session:
        user = await UserRepository(session).get_by_telegram_id(
            callback.from_user.id
        )
    if user is None:
        await callback.answer("Сначала нажмите /start.", show_alert=True)
        return
    try:
        link = await _referral_link(callback, user.referral_code)
    except RuntimeError:
        await callback.answer(
            "Ссылка временно недоступна. Попробуйте позже.", show_alert=True
        )
        return
    await callback.answer("Ссылка отправлена отдельным сообщением.")
    if callback.message:
        await callback.message.answer(link)
