from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path

from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.client.telegram import TelegramAPIServer
from aiogram.exceptions import TelegramAPIError
from aiohttp import ClientError, ClientSession
from pydantic import ValidationError
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from app.bot.middlewares.database import DatabaseUserMiddleware
from app.bot.routers import admin_router, downloader_router, start_router
from app.config import Settings, get_settings
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.requests import DownloadRequestRepository
from app.database.session import Database
from app.services.cleanup import TempStorage, cleanup_loop, request_cleanup_loop
from app.services.cobalt.client import CobaltClient
from app.services.cobalt.security import SafeRemoteUrlValidator
from app.services.download_manager import DownloadManager
from app.services.media.backends.cobalt import AiohttpResponseOpener, CobaltBackend
from app.services.media.backends.router import MediaBackendRouter
from app.services.media.backends.ytdlp import YtDlpBackend
from app.services.media.downloader import MediaDownloader
from app.services.media.metadata import YtDlpMetadataService
from app.services.media.models import MediaSource
from app.services.media.registry import MediaRegistry
from app.services.queue import DownloadQueue
from app.services.rate_limit import SlidingWindowRateLimiter
from app.services.statistics import StatisticsService
from app.services.subscriptions import SubscriptionService
from app.services.telegram_files import TelegramFileService
from app.utils.logging import configure_logging

logger = logging.getLogger(__name__)
READY_FILE = Path("/tmp/bot-ready")  # noqa: S108 -- container-local health marker


def create_bot(settings: Settings) -> Bot:
    token = settings.bot_token.get_secret_value()
    if settings.telegram_api_mode == "local":
        api_server = TelegramAPIServer.from_base(
            settings.telegram_local_api_url,
            is_local=True,
        )
        session = AiohttpSession(api=api_server, timeout=120)
        return Bot(token=token, session=session)
    return Bot(token=token)


async def run() -> int:
    try:
        settings = get_settings()
    except ValidationError as exc:
        print(f"Configuration error: {exc.error_count()} invalid setting(s).", file=sys.stderr)
        return 2
    configure_logging(
        settings.log_level,
        secrets=(
            settings.bot_token.get_secret_value(),
            settings.telegram_api_hash.get_secret_value(),
            settings.database_url.get_secret_value(),
        ),
    )
    try:
        database = Database(settings.database_url.get_secret_value())
    except (SQLAlchemyError, ValueError):
        logger.error("DATABASE_URL is invalid.")
        return 4
    bot = create_bot(settings)
    storage = TempStorage(settings.temp_root)
    queue = DownloadQueue(
        settings.max_concurrent_downloads,
        settings.max_downloads_per_user,
    )
    stop_cleanup = asyncio.Event()
    cleanup_tasks: list[asyncio.Task[None]] = []
    cobalt_http_session: ClientSession | None = None
    try:
        try:
            async with database.engine.connect() as connection:
                await connection.execute(text("SELECT 1"))
        except (SQLAlchemyError, OSError):
            logger.error("PostgreSQL is unavailable.")
            return 4
        try:
            me = await bot.get_me()
        except (TelegramAPIError, ClientError, OSError):
            logger.error("Telegram Local Bot API is unavailable.")
            return 3
        logger.info("Connected to Telegram Local Bot API as bot id=%s", me.id)
        removed = await storage.cleanup_stale(settings.temp_file_ttl_hours)
        logger.info("Startup cleanup removed %d stale job directories", removed)
        async with database.session() as session:
            expired = await DownloadRequestRepository(session).cleanup_expired(
                settings.download_request_ttl_minutes
            )
            cancelled = await DownloadRepository(session).cancel_incomplete_after_restart()
        logger.info("Startup cleanup removed %d expired download requests", expired)
        logger.info("Marked %d interrupted downloads as cancelled", cancelled)
        await queue.start()
        cleanup_tasks.extend(
            [
                asyncio.create_task(
                    cleanup_loop(
                        storage,
                        ttl_hours=settings.temp_file_ttl_hours,
                        interval_seconds=settings.cleanup_interval_seconds,
                        stop_event=stop_cleanup,
                    ),
                    name="temp-cleanup",
                ),
                asyncio.create_task(
                    request_cleanup_loop(
                        database,
                        ttl_minutes=settings.download_request_ttl_minutes,
                        interval_seconds=settings.cleanup_interval_seconds,
                        stop_event=stop_cleanup,
                    ),
                    name="request-cleanup",
                ),
            ]
        )

        cookies = str(settings.ytdlp_cookies_file) if settings.ytdlp_cookies_file else None
        subscriptions = SubscriptionService()
        media_registry = MediaRegistry.default()
        ytdlp_backend = YtDlpBackend(MediaDownloader(cookies))
        tiktok_backends = [ytdlp_backend]
        cobalt_client: CobaltClient | None = None
        if settings.cobalt_enabled:
            cobalt_http_session = ClientSession()
            cobalt_validator = SafeRemoteUrlValidator()
            cobalt_client = CobaltClient(
                base_url=settings.cobalt_api_url,
                request_timeout_seconds=settings.cobalt_request_timeout_seconds,
                session=cobalt_http_session,
                validator=cobalt_validator,
            )
            cobalt_backend = CobaltBackend(
                client=cobalt_client,
                validator=cobalt_validator,
                opener=AiohttpResponseOpener(
                    cobalt_http_session,
                    settings.cobalt_stream_timeout_seconds,
                ),
                max_upload_bytes=settings.max_upload_bytes,
            )
            tiktok_backends.insert(0, cobalt_backend)
        backend_router = MediaBackendRouter(
            {
                MediaSource.YOUTUBE: (ytdlp_backend,),
                MediaSource.TIKTOK: tuple(tiktok_backends),
            }
        )
        manager = DownloadManager(
            database=database,
            settings=settings,
            storage=storage,
            backend_router=backend_router,
            subscriptions=subscriptions,
            telegram_files=TelegramFileService(),
        )
        dispatcher = Dispatcher()
        dispatcher.update.outer_middleware(DatabaseUserMiddleware(database))
        dispatcher.include_routers(start_router, admin_router, downloader_router)
        dispatcher.workflow_data.update(
            settings=settings,
            media_registry=media_registry,
            metadata_service=YtDlpMetadataService(
                registry=media_registry,
                timeout_seconds=settings.metadata_timeout_seconds,
                max_upload_bytes=settings.max_upload_bytes,
                cookies_file=cookies,
            ),
            rate_limiter=SlidingWindowRateLimiter(
                settings.rate_limit_urls,
                settings.rate_limit_window_seconds,
            ),
            subscription_service=subscriptions,
            statistics_service=StatisticsService(),
            download_queue=queue,
            download_manager=manager,
            cobalt_client=cobalt_client,
        )
        await asyncio.to_thread(READY_FILE.touch)
        await dispatcher.start_polling(
            bot,
            allowed_updates=dispatcher.resolve_used_update_types(),
            close_bot_session=False,
        )
        return 0
    finally:
        await asyncio.to_thread(READY_FILE.unlink, missing_ok=True)
        stop_cleanup.set()
        if cleanup_tasks:
            await asyncio.gather(*cleanup_tasks, return_exceptions=True)
        await queue.stop()
        if cobalt_http_session is not None:
            await cobalt_http_session.close()
        await bot.session.close()
        await database.close()


def main() -> None:
    raise SystemExit(asyncio.run(run()))


if __name__ == "__main__":
    main()
