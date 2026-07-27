from aiogram import Router

from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.apps import router as apps_router
from app.bot.handlers.bonuses import router as bonuses_router
from app.bot.handlers.promos import router as promos_router
from app.bot.handlers.remnawave_admin import router as remnawave_admin_router
from app.bot.handlers.start import router as start_router
from app.bot.handlers.support import control_router as support_control_router
from app.bot.handlers.support import user_router as support_user_router
from app.bot.handlers.tariffs import router as tariffs_router
from app.bot.handlers.trial import router as trial_router
from app.bot.handlers.user_commands import (
    router as user_commands_router,
)
from app.bot.handlers.user_commands import (
    unknown_router,
)


def setup_routers() -> Router:
    router = Router(name="root")
    router.include_router(start_router)
    # Commands go first so they can intentionally replace a conflicting FSM
    # flow (notably /promo). The unknown-command fallback remains last.
    router.include_router(user_commands_router)
    router.include_router(support_control_router)
    router.include_router(remnawave_admin_router)
    router.include_router(admin_router)
    router.include_router(promos_router)
    router.include_router(apps_router)
    router.include_router(bonuses_router)
    router.include_router(trial_router)
    router.include_router(tariffs_router)
    # Active FSM flows (promo/top-up/admin forms) keep priority over the
    # support catch-all, while unknown commands remain excluded and fall
    # through to their existing handler.
    router.include_router(support_user_router)
    router.include_router(unknown_router)
    return router
