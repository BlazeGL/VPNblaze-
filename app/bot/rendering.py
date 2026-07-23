from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InlineKeyboardMarkup, Message


async def edit_text_or_caption(
    message: Message,
    text: str,
    reply_markup: InlineKeyboardMarkup,
    *,
    parse_mode: ParseMode | None = None,
) -> None:
    """Edit a text or photo message and only send a new one as a fallback."""
    try:
        if message.photo:
            await message.edit_caption(
                caption=text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
        else:
            await message.edit_text(
                text,
                reply_markup=reply_markup,
                parse_mode=parse_mode,
            )
    except TelegramBadRequest as exc:
        if "message is not modified" in str(exc).lower():
            return
        await message.answer(
            text,
            reply_markup=reply_markup,
            parse_mode=parse_mode,
        )
