from aiogram.filters import BaseFilter
from aiogram.types import CallbackQuery, Message


class AdminFilter(BaseFilter):
    async def __call__(
        self, event: Message | CallbackQuery, admin_ids: set[int]
    ) -> bool:
        return event.from_user is not None and event.from_user.id in admin_ids
