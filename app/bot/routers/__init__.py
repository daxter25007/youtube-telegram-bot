from app.bot.routers.admin import router as admin_router
from app.bot.routers.downloader import router as downloader_router
from app.bot.routers.start import router as start_router

__all__ = ["admin_router", "downloader_router", "start_router"]
