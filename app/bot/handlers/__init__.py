from aiogram import Router

from app.bot.handlers.start import router as start_router


def setup_routers() -> Router:
    router = Router(name="root")
    router.include_router(start_router)
    return router
