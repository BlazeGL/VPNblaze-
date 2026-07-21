from unittest.mock import AsyncMock, MagicMock

import pytest

from app.database.models import User
from app.database.repositories import UserRepository


def make_session(result: MagicMock) -> MagicMock:
    session = MagicMock()
    session.execute = AsyncMock(return_value=result)
    session.flush = AsyncMock()
    return session


@pytest.mark.asyncio
async def test_get_by_telegram_id_returns_user() -> None:
    user = User(telegram_id=42)
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session = make_session(result)

    found = await UserRepository(session).get_by_telegram_id(42)

    assert found is user
    session.execute.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_or_create_creates_missing_user() -> None:
    result = MagicMock()
    result.scalar_one_or_none.return_value = None
    session = make_session(result)
    repository = UserRepository(session)

    user, created = await repository.get_or_create(
        42, "alice", "Alice", "Example", is_admin=True
    )

    assert created is True
    assert user.telegram_id == 42
    assert user.is_admin is True
    session.add.assert_called_once_with(user)
    session.flush.assert_awaited_once()


@pytest.mark.asyncio
async def test_get_or_create_updates_existing_user() -> None:
    user = User(
        telegram_id=42,
        username="old",
        first_name="Old",
        last_name=None,
    )
    result = MagicMock()
    result.scalar_one_or_none.return_value = user
    session = make_session(result)

    updated, created = await UserRepository(session).get_or_create(
        42, "new", "New", "Name"
    )

    assert created is False
    assert updated is user
    assert (user.username, user.first_name, user.last_name) == ("new", "New", "Name")
    assert user.last_activity_at is not None
    session.flush.assert_awaited_once()
