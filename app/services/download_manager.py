from __future__ import annotations

import asyncio
import html
import logging
import os
import time
from pathlib import Path

from aiogram import Bot
from aiogram.exceptions import TelegramAPIError, TelegramBadRequest
from aiogram.types import Message

from app.config import Settings
from app.database.models import DownloadStatus
from app.database.repositories.channels import ChannelRepository
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.media_cache import MediaCacheRepository
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
from app.utils.filesize import is_too_large

logger = logging.getLogger(__name__)
TELEGRAM_CAPTION_MAX_UTF16 = 1024
INVALID_CACHED_FILE_MARKERS = (
    "wrong file identifier",
    "wrong remote file identifier",
    "invalid file id",
    "file_id",
    "file reference expired",
    "file_reference_expired",
)


def _utf16_length(value: str) -> int:
    return len(value.encode("utf-16-le")) // 2


def _truncate_utf16(value: str, max_units: int) -> str:
    if _utf16_length(value) <= max_units:
        return value
    if max_units <= 0:
        return ""
    remaining = max_units - 1
    result: list[str] = []
    for character in value:
        units = _utf16_length(character)
        if units > remaining:
            break
        result.append(character)
        remaining -= units
    return f"{''.join(result)}…"


def format_video_caption(
    *,
    title: str,
    source_url: str,
    height: int | None,
    bot_username: str,
) -> str:
    username = bot_username.lstrip("@")
    visible_without_title = f"🍿 \n\n🔗 {source_url}\n\n"
    if height is not None:
        visible_without_title += f"📺 {height}p\n\n"
    visible_without_title += f"\u200b💙 @{username}"
    title_budget = TELEGRAM_CAPTION_MAX_UTF16 - _utf16_length(visible_without_title)
    safe_title = html.escape(_truncate_utf16(title, title_budget))
    safe_url = html.escape(source_url, quote=True)
    safe_username = html.escape(username)
    lines = [
        f"🍿 <code>{safe_title}</code>",
        f'🔗 <a href="{safe_url}">{safe_url}</a>',
    ]
    if height is not None:
        lines.append(f"📺 {height}p")
    lines.append(f'<a href="{safe_url}">&#8203;</a>💙 @{safe_username}')
    return "\n\n".join(lines)


def _is_invalid_cached_file_error(error: TelegramBadRequest) -> bool:
    message = str(error).casefold()
    return any(marker in message for marker in INVALID_CACHED_FILE_MARKERS)


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
        bot_username: str,
    ) -> None:
        self.database = database
        self.settings = settings
        self.storage = storage
        self.backend_router = backend_router
        self.subscriptions = subscriptions
        self.telegram_files = telegram_files
        self.bot_username = bot_username

    async def cancel_queued(self, download_id: int, progress_message: Message) -> None:
        await self._status(download_id, DownloadStatus.CANCELLED, error_type="shutdown")
        try:
            await progress_message.edit_text("⌛ Загрузка отменена: бот завершает работу.")
        except TelegramAPIError:
            logger.debug("Queued cancellation message could not be edited")

    async def try_cached(
        self,
        *,
        bot: Bot,
        chat_id: int,
        user_id: int,
        download_id: int,
        source: MediaSource,
        source_media_id: str,
        media_kind: MediaKind,
        option_key: str,
        title: str,
        source_url: str,
        progress_message: Message,
    ) -> bool:
        started = time.monotonic()
        async with self.database.session() as session:
            cached = await MediaCacheRepository(session).get(
                source=source,
                source_media_id=source_media_id,
                media_kind=media_kind,
                requested_option=option_key,
            )
        if cached is None:
            return False

        try:
            await self.telegram_files.send_cached(
                bot,
                chat_id=chat_id,
                file_id=cached.telegram_file_id,
                sent_as_document=cached.sent_as_document,
                caption=format_video_caption(
                    title=title,
                    source_url=source_url,
                    height=cached.actual_quality,
                    bot_username=self.bot_username,
                ),
            )
        except TelegramBadRequest as error:
            if not _is_invalid_cached_file_error(error):
                raise
            logger.info(
                "Discarding invalid Telegram cache source=%s media_id=%s option=%s",
                source.value,
                source_media_id,
                option_key,
            )
            async with self.database.session() as session:
                await MediaCacheRepository(session).delete(cached.id)
            return False
        async with self.database.session() as session:
            await MediaCacheRepository(session).touch(cached.id)
            await DownloadRepository(session).set_status(
                download_id,
                DownloadStatus.COMPLETED,
                actual_quality=cached.actual_quality,
                file_size=cached.file_size,
                processing_seconds=time.monotonic() - started,
                cache_hit=True,
            )
            await UserRepository(session).increment_downloads(user_id)
        try:
            await progress_message.delete()
        except TelegramAPIError:
            logger.debug("Cached progress message could not be deleted")
        return True

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
        source_media_id: str,
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
                upload_result = await self.telegram_files.send(
                    bot,
                    chat_id=chat_id,
                    package=package,
                    caption=format_video_caption(
                        title=title,
                        source_url=source_url,
                        height=primary.height,
                        bot_username=self.bot_username,
                    ),
                )
                elapsed = time.monotonic() - started
                async with self.database.session() as session:
                    await MediaCacheRepository(session).store(
                        source=source,
                        source_media_id=source_media_id,
                        media_kind=media_kind,
                        requested_option=option_key,
                        telegram_file_id=upload_result.file_id,
                        telegram_file_unique_id=upload_result.file_unique_id,
                        sent_as_document=upload_result.sent_as_document,
                        actual_quality=primary.height,
                        file_size=file_size,
                    )
                    await DownloadRepository(session).set_status(
                        download_id,
                        DownloadStatus.COMPLETED,
                        actual_quality=primary.height,
                        file_size=file_size,
                        processing_seconds=elapsed,
                        backend=result.backend,
                    )
                    await UserRepository(session).increment_downloads(user_id)
                try:
                    await progress_message.delete()
                except TelegramAPIError:
                    logger.debug("Completed progress message could not be deleted")
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
