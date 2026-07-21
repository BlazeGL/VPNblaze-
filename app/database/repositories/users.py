from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import User


class UserRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_by_telegram_id(self, telegram_id: int) -> User | None:
        result = await self.session.execute(
            select(User).where(User.telegram_id == telegram_id)
        )
        return result.scalar_one_or_none()

    async def create(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        *,
        is_admin: bool = False,
    ) -> User:
        user = User(
            telegram_id=telegram_id,
            username=username,
            first_name=first_name,
            last_name=last_name,
            is_admin=is_admin,
        )
        self.session.add(user)
        await self.session.flush()
        return user

    async def get_or_create(
        self,
        telegram_id: int,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
        *,
        is_admin: bool = False,
    ) -> tuple[User, bool]:
        user = await self.get_by_telegram_id(telegram_id)
        if user is not None:
            await self.update_telegram_data(user, username, first_name, last_name)
            return user, False
        return (
            await self.create(
                telegram_id,
                username,
                first_name,
                last_name,
                is_admin=is_admin,
            ),
            True,
        )

    async def update_telegram_data(
        self,
        user: User,
        username: str | None,
        first_name: str | None,
        last_name: str | None,
    ) -> User:
        user.username = username
        user.first_name = first_name
        user.last_name = last_name
        user.last_activity_at = datetime.now(UTC)
        await self.session.flush()
        return user
