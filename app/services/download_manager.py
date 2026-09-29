from __future__ import annotations

import asyncio
import logging
import os
import time
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError
from aiogram.types import Message

from app.config import Settings
from app.database.models import DownloadStatus
from app.database.repositories.channels import ChannelRepository
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.users import UserRepository
from app.database.session import Database
from app.services.cleanup import TempStorage
from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    DownloadBackend,
    MediaDownloadRequest,
)
from app.services.media.backends.router import MediaBackendRouter
from app.services.media.models import DownloadPolicy, MediaKind, MediaSource
from app.services.subscriptions import SubscriptionService
from app.services.telegram_files import TelegramFileService
from app.utils.filenames import sanitize_display_filename
from app.utils.filesize import is_too_large

logger = logging.getLogger(__name__)


class ProgressReporter:
    def __init__(self, message: Message, min_interval: float) -> None:
        self.message = message
        self.min_interval = min_interval
        self._last_update = 0.0
        self._last_text = ""

    async def update(
        self, stage: str, percent: float | None = None, *, force: bool = False
    ) -> None:
        if stage == "downloading":
            text = (
                f"⬇️ Скачиваю видео: {percent:.0f}%"
                if percent is not None
                else "⬇️ Скачиваю видео..."
            )
        elif stage == "merging":
            text = "🎬 Обрабатываю видео..."
        else:
            text = stage
        now = time.monotonic()
        if not force and now - self._last_update < self.min_interval:
            return
        if text == self._last_text:
            return
        try:
            await self.message.edit_text(text)
            self._last_update = now
            self._last_text = text
        except TelegramAPIError:
            logger.debug("Progress message could not be edited")


class DownloadManager:
    def __init__(
        self,
        *,
        database: Database,
        settings: Settings,
        storage: TempStorage,
        backend_router: MediaBackendRouter,
        subscriptions: SubscriptionService,
        telegram_files: TelegramFileService,
    ) -> None:
        self.database = database
        self.settings = settings
        self.storage = storage
        self.backend_router = backend_router
        self.subscriptions = subscriptions
        self.telegram_files = telegram_files

    async def cancel_queued(self, download_id: int, progress_message: Message) -> None:
        await self._status(download_id, DownloadStatus.CANCELLED, error_type="shutdown")
        try:
            await progress_message.edit_text("⌛ Загрузка отменена: бот завершает работу.")
        except TelegramAPIError:
            logger.debug("Queued cancellation message could not be edited")

    async def run(
        self,
        *,
        bot: Bot,
        chat_id: int,
        telegram_user_id: int,
        user_id: int,
        download_id: int,
        source_url: str,
        source: MediaSource,
        media_kind: MediaKind,
        option_key: str,
        policy: DownloadPolicy,
        title: str,
        progress_message: Message,
    ) -> None:
        started = time.monotonic()
        job_dir: Path | None = None
        reporter = ProgressReporter(progress_message, self.settings.progress_update_seconds)
        try:
            async with self.database.session() as session:
                channels = await ChannelRepository(session).active()
            missing = await self.subscriptions.missing_channels(bot, telegram_user_id, channels)
            if missing:
                await self._status(download_id, DownloadStatus.CANCELLED, error_type="subscription")
                await reporter.update(
                    "Для загрузки необходимо оставаться подписанным на обязательные каналы.",
                    force=True,
                )
                return

            job_dir = await self.storage.create_job()
            async with asyncio.timeout(self.settings.download_timeout_seconds):
                await self._status(download_id, DownloadStatus.DOWNLOADING)

                async def progress(stage: str, percent: float | None) -> None:
                    if stage == "merging":
                        await self._status(download_id, DownloadStatus.MERGING)
                    await reporter.update(stage, percent)

                async def on_fallback(
                    failed: DownloadBackend,
                    following: DownloadBackend,
                ) -> None:
                    logger.info(
                        "Switching media backend download_id=%s source=%s from=%s to=%s",
                        download_id,
                        source.value,
                        failed.value,
                        following.value,
                    )
                    await reporter.update(
                        "🔄 Основной способ недоступен. Пробую резервный способ...",
                        force=True,
                    )

                result = await self.backend_router.download(
                    MediaDownloadRequest(
                        source=source,
                        url=source_url,
                        option_key=option_key,
                        policy=policy,
                    ),
                    job_dir,
                    progress,
                    on_fallback,
                )
                package = result.package
                if not package.artifacts:
                    raise BackendError(
                        "Загрузчик вернул пустой пакет",
                        kind=BackendErrorKind.RETRYABLE,
                    )
                file_sizes = [
                    await asyncio.to_thread(os.path.getsize, artifact.path)
                    for artifact in package.artifacts
                ]
                file_size = sum(file_sizes)
                primary = package.artifacts[0]
                if any(is_too_large(size, self.settings.max_upload_bytes) for size in file_sizes):
                    await self._status(
                        download_id,
                        DownloadStatus.TOO_LARGE,
                        file_size=file_size,
                        actual_quality=primary.height,
                        processing_seconds=time.monotonic() - started,
                    )
                    await reporter.update(
                        "⚠️ Видео получилось слишком большим для отправки в Telegram. "
                        "Выберите более низкое качество.",
                        force=True,
                    )
                    return
                await self._status(download_id, DownloadStatus.UPLOADING, file_size=file_size)
                await reporter.update("📤 Отправляю в Telegram...", force=True)
                await self.telegram_files.send(
                    bot,
                    chat_id=chat_id,
                    package=package,
                    display_filename=sanitize_display_filename(
                        title, primary.path.suffix or ".mp4"
                    ),
                    caption=(
                        f"🎬 {title}\n📺 {primary.height}p" if primary.height else f"🎬 {title}"
                    ),
                )
                elapsed = time.monotonic() - started
                async with self.database.session() as session:
                    await DownloadRepository(session).set_status(
                        download_id,
                        DownloadStatus.COMPLETED,
                        actual_quality=primary.height,
                        file_size=file_size,
                        processing_seconds=elapsed,
                    )
                    await UserRepository(session).increment_downloads(user_id)
                await reporter.update("✅ Готово", force=True)
        except TimeoutError:
            await self._status(
                download_id,
                DownloadStatus.TIMEOUT,
                error_type="timeout",
                processing_seconds=time.monotonic() - started,
            )
            await reporter.update(
                "⌛ Загрузка заняла слишком много времени и была остановлена.", force=True
            )
        except asyncio.CancelledError:
            await asyncio.shield(
                self._status(
                    download_id,
                    DownloadStatus.CANCELLED,
                    error_type="shutdown",
                    processing_seconds=time.monotonic() - started,
                )
            )
            raise
        except BackendError as error:
            logger.warning(
                "Media download failed download_id=%s source=%s kind=%s option=%s error_kind=%s",
                download_id,
                source.value,
                media_kind.value,
                option_key,
                error.kind.value,
            )
            if error.kind is BackendErrorKind.TOO_LARGE:
                await self._status(
                    download_id,
                    DownloadStatus.TOO_LARGE,
                    error_type=error.kind.value,
                    processing_seconds=time.monotonic() - started,
                )
                await reporter.update(
                    "⚠️ Видео получилось слишком большим для отправки в Telegram. "
                    "Выберите более низкое качество.",
                    force=True,
                )
                return
            await self._status(
                download_id,
                DownloadStatus.FAILED,
                error_type=f"backend_{error.kind.value}",
                processing_seconds=time.monotonic() - started,
            )
            await reporter.update("❌ Не удалось скачать видео. Попробуйте позже.", force=True)
        except TelegramAPIError:
            logger.exception("Telegram upload failed for download_id=%s", download_id)
            await self._status(
                download_id,
                DownloadStatus.FAILED,
                error_type="telegram_api",
                processing_seconds=time.monotonic() - started,
            )
            await reporter.update("❌ Не удалось отправить файл в Telegram.", force=True)
        except Exception:
            logger.exception("Unexpected download failure for download_id=%s", download_id)
            await self._status(
                download_id,
                DownloadStatus.FAILED,
                error_type="internal",
                processing_seconds=time.monotonic() - started,
            )
            await reporter.update("❌ Не удалось скачать видео. Попробуйте позже.", force=True)
        finally:
            if job_dir is not None:
                await asyncio.shield(self.storage.remove_job(job_dir))

    async def _status(
        self,
        download_id: int,
        status: DownloadStatus,
        **values: object,
    ) -> None:
        async with self.database.session() as session:
            await DownloadRepository(session).set_status(download_id, status, **values)  # type: ignore[arg-type]
