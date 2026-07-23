import asyncio
import logging
import os
import subprocess
from decimal import Decimal
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import get_settings
from app.database.models import Tariff
from app.database.session import create_engine_and_session

logger = logging.getLogger(__name__)

DEFAULT_TARIFF = {
    "name": "BlazeVPN — 30 дней",
    "description": None,
    "duration_days": 30,
    "price": Decimal("99.00"),
    "currency": "RUB",
    "traffic_limit_gb": 600,
    "is_unlimited_traffic": False,
    "device_limit": 3,
    "is_active": True,
    "sort_order": 10,
}
DEFAULT_STATE_FILE = "/var/lib/blazevpn-state/database-initialized"


async def ensure_initial_data(session: AsyncSession) -> bool:
    """Create defaults only for a genuinely empty installation.

    Existing tariffs are business data. They must never be updated or duplicated
    by deployments.
    """
    existing_tariff = await session.scalar(select(Tariff.id).limit(1))
    if existing_tariff is not None:
        return False
    session.add(Tariff(**DEFAULT_TARIFF))
    await session.flush()
    return True


async def database_has_application_schema(database_url: str) -> bool:
    engine, _ = create_engine_and_session(database_url)
    try:
        async with engine.connect() as connection:
            return bool(
                await connection.scalar(
                    text(
                        """
                        SELECT EXISTS (
                            SELECT 1
                            FROM pg_catalog.pg_tables
                            WHERE schemaname = current_schema()
                              AND tablename IN ('alembic_version', 'users')
                        )
                        """
                    )
                )
            )
    finally:
        await engine.dispose()


def run_alembic_upgrade() -> None:
    subprocess.run(["alembic", "upgrade", "head"], check=True)


def state_file_exists(state_file: Path) -> bool:
    return state_file.exists()


def mark_database_initialized(state_file: Path) -> None:
    state_file.parent.mkdir(parents=True, exist_ok=True)
    state_file.touch(exist_ok=True)


async def startup() -> None:
    settings = get_settings()
    state_file = Path(
        os.environ.get("DATABASE_STATE_FILE", DEFAULT_STATE_FILE)
    )
    schema_exists = await database_has_application_schema(settings.database_url)
    if state_file_exists(state_file) and not schema_exists:
        raise RuntimeError(
            "Refusing to initialize an empty PostgreSQL database: this deployment "
            "was already initialized. Restore the original Docker volume or a "
            "backup before starting BlazeVPN."
        )

    run_alembic_upgrade()

    engine, session_factory = create_engine_and_session(settings.database_url)
    try:
        async with session_factory() as session, session.begin():
            created = await ensure_initial_data(session)
            if created:
                logger.info("Created the missing default tariff")
            else:
                logger.info("Existing tariffs left unchanged")
    finally:
        await engine.dispose()

    mark_database_initialized(state_file)


if __name__ == "__main__":
    asyncio.run(startup())
