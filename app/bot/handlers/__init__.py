from aiogram import Router

from app.bot.handlers.admin import router as admin_router
from app.bot.handlers.promos import router as promos_router
from app.bot.handlers.remnawave_admin import router as remnawave_admin_router
from app.bot.handlers.start import router as start_router
from app.bot.handlers.tariffs import router as tariffs_router
from app.bot.handlers.trial import router as trial_router


def setup_routers() -> Router:
    router = Router(name="root")
    router.include_router(remnawave_admin_router)
    router.include_router(admin_router)
    router.include_router(promos_router)
    router.include_router(trial_router)
    router.include_router(tariffs_router)
    router.include_router(start_router)
    return router
