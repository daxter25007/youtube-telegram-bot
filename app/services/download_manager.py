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
from app.database.repositories.media_manifests import MediaManifestRepository
from app.database.repositories.users import UserRepository
from app.database.session import Database
from app.services.cleanup import TempStorage
from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    DownloadBackend,
    MediaDownloadRequest,
    MediaPartFailure,
    PreparedMedia,
)
from app.services.media.backends.router import MediaBackendRouter
from app.services.media.models import (
    QUALITY_SELECTION_SOURCES,
    DownloadPolicy,
    MediaArtifact,
    MediaKind,
    MediaPackage,
    MediaPart,
    MediaSource,
)
from app.services.subscriptions import SubscriptionService
from app.services.telegram_files import (
    InvalidCachedPartError,
    TelegramFileService,
    TelegramMediaInput,
    UploadResult,
)
from app.utils.audit import audit_event
from app.utils.filesize import is_too_large

logger = logging.getLogger(__name__)
PUBLICATION_SOURCES = frozenset({MediaSource.TIKTOK, MediaSource.INSTAGRAM, MediaSource.PINTEREST})
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
        audit_event(
            "download_finished",
            download_id=download_id,
            status=DownloadStatus.CANCELLED,
            outcome="shutdown_before_start",
        )
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
            audit_event(
                "download_cache_miss",
                download_id=download_id,
                chat_id=chat_id,
                source=source,
                source_media_id=source_media_id,
                media_kind=media_kind,
                option=option_key,
            )
            return False

        try:
            await self.telegram_files.send_cached(
                bot,
                chat_id=chat_id,
                part_key=option_key,
                media_kind=media_kind,
                file_id=cached.telegram_file_id,
                sent_as_document=cached.sent_as_document,
                caption=format_video_caption(
                    title=title,
                    source_url=source_url,
                    height=cached.actual_quality,
                    bot_username=self.bot_username,
                ),
            )
        except (InvalidCachedPartError, TelegramBadRequest) as error:
            if isinstance(error, TelegramBadRequest) and not _is_invalid_cached_file_error(error):
                raise
            logger.info(
                "Discarding invalid Telegram cache source=%s media_id=%s option=%s",
                source.value,
                source_media_id,
                option_key,
            )
            async with self.database.session() as session:
                await MediaCacheRepository(session).delete(cached.id)
            audit_event(
                "download_cache_invalid",
                download_id=download_id,
                source=source,
                source_media_id=source_media_id,
                option=option_key,
            )
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
        elapsed = time.monotonic() - started
        audit_event(
            "download_finished",
            download_id=download_id,
            chat_id=chat_id,
            source=source,
            source_media_id=source_media_id,
            media_kind=media_kind,
            option=option_key,
            title=title,
            status=DownloadStatus.COMPLETED,
            outcome="cache_hit",
            file_count=1,
            total_bytes=cached.file_size,
            duration_ms=round(elapsed * 1000),
        )
        return True

    async def try_cached_publication(
        self,
        *,
        bot: Bot,
        chat_id: int,
        user_id: int,
        download_id: int,
        source: MediaSource = MediaSource.TIKTOK,
        source_media_id: str,
        title: str,
        source_url: str,
        progress_message: Message,
    ) -> bool:
        started = time.monotonic()
        async with self.database.session() as session:
            manifest = await MediaManifestRepository(session).get(
                source=source,
                source_media_id=source_media_id,
            )
            if manifest is None:
                audit_event(
                    "download_cache_miss",
                    download_id=download_id,
                    chat_id=chat_id,
                    source=source,
                    source_media_id=source_media_id,
                    reason="manifest_missing",
                )
                return False
            parts = manifest.parts
            identities = {(part.media_kind, part.key) for part in parts}
            cached = await MediaCacheRepository(session).get_many(
                source=source,
                source_media_id=source_media_id,
                identities=identities,
            )
        if len(cached) != len(parts):
            audit_event(
                "download_cache_miss",
                download_id=download_id,
                chat_id=chat_id,
                source=source,
                source_media_id=source_media_id,
                reason="incomplete_publication",
                cached_parts=len(cached),
                expected_parts=len(parts),
            )
            return False
        caption = format_video_caption(
            title=title,
            source_url=source_url,
            height=None,
            bot_username=self.bot_username,
        )
        try:
            _, delivery_failures = await self._deliver_parts(
                bot=bot,
                chat_id=chat_id,
                parts=parts,
                cached=cached,
                artifacts={},
                caption=caption,
            )
            if delivery_failures:
                audit_event(
                    "download_cache_invalid",
                    download_id=download_id,
                    source=source,
                    source_media_id=source_media_id,
                    failed_parts=len(delivery_failures),
                )
                return False
        except InvalidCachedPartError as error:
            invalid = next(
                (item for (kind, key), item in cached.items() if key == error.part_key),
                None,
            )
            if invalid is not None:
                async with self.database.session() as session:
                    await MediaCacheRepository(session).delete(invalid.id)
            audit_event(
                "download_cache_invalid",
                download_id=download_id,
                source=source,
                source_media_id=source_media_id,
                part_key=error.part_key,
            )
            return False
        async with self.database.session() as session:
            repository = MediaCacheRepository(session)
            for item in cached.values():
                await repository.touch(item.id)
            await DownloadRepository(session).set_status(
                download_id,
                DownloadStatus.COMPLETED,
                processing_seconds=time.monotonic() - started,
                cache_hit=True,
            )
            await UserRepository(session).increment_downloads(user_id)
        try:
            await progress_message.delete()
        except TelegramAPIError:
            logger.debug("Cached publication progress message could not be deleted")
        elapsed = time.monotonic() - started
        audit_event(
            "download_finished",
            download_id=download_id,
            chat_id=chat_id,
            source=source,
            source_media_id=source_media_id,
            title=title,
            status=DownloadStatus.COMPLETED,
            outcome="cache_hit",
            file_count=len(cached),
            total_bytes=sum(item.file_size or 0 for item in cached.values()),
            duration_ms=round(elapsed * 1000),
        )
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
        audit_event(
            "download_started",
            telegram_user_id=telegram_user_id,
            chat_id=chat_id,
            download_id=download_id,
            source=source,
            source_media_id=source_media_id,
            media_kind=media_kind,
            option=option_key,
            title=title,
        )
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
                audit_event(
                    "download_finished",
                    telegram_user_id=telegram_user_id,
                    download_id=download_id,
                    status=DownloadStatus.CANCELLED,
                    outcome="subscription_missing",
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
                    audit_event(
                        "download_backend_fallback",
                        telegram_user_id=telegram_user_id,
                        download_id=download_id,
                        source=source,
                        source_media_id=source_media_id,
                        failed_backend=failed,
                        next_backend=following,
                    )

                prepared_parts: tuple[MediaPart, ...] = ()
                cached_parts: dict[tuple[MediaKind, str], object] = {}

                async def on_prepared(prepared: PreparedMedia) -> frozenset[str]:
                    nonlocal prepared_parts, cached_parts
                    async with self.database.session() as session:
                        manifest = None
                        if source in PUBLICATION_SOURCES:
                            manifest_repository = MediaManifestRepository(session)
                            manifest = await manifest_repository.get(
                                source=source, source_media_id=source_media_id
                            )
                        manifest_visuals = (
                            [
                                part
                                for part in manifest.parts
                                if part.media_kind
                                in {MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.ANIMATION}
                            ]
                            if manifest
                            else []
                        )
                        prepared_visuals = [
                            part
                            for part in prepared.parts
                            if part.media_kind
                            in {MediaKind.PHOTO, MediaKind.VIDEO, MediaKind.ANIMATION}
                        ]
                        known_richer_manifest = bool(
                            manifest and len(manifest_visuals) > len(prepared_visuals)
                        )
                        prepared_parts = manifest.parts if known_richer_manifest else prepared.parts
                        identities = {(part.media_kind, part.key) for part in prepared_parts}
                        if source in PUBLICATION_SOURCES and not known_richer_manifest:
                            await MediaManifestRepository(session).upsert(
                                source=source,
                                source_media_id=source_media_id,
                                parts=prepared_parts,
                            )
                        cached_parts = await MediaCacheRepository(session).get_many(
                            source=source,
                            source_media_id=source_media_id,
                            identities=identities,
                        )
                    audit_event(
                        "download_prepared",
                        telegram_user_id=telegram_user_id,
                        download_id=download_id,
                        source=source,
                        source_media_id=source_media_id,
                        part_count=len(prepared_parts),
                        cached_part_count=len(cached_parts),
                        parts=",".join(
                            f"{part.position}:{part.media_kind.value}:{part.key}"
                            for part in prepared_parts
                        ),
                    )
                    validator = getattr(self.telegram_files, "validate_cached", None)
                    if validator is not None:
                        for identity, cached_item in tuple(cached_parts.items()):
                            try:
                                await validator(
                                    bot,
                                    part_key=identity[1],
                                    file_id=cached_item.telegram_file_id,
                                )
                            except InvalidCachedPartError:
                                async with self.database.session() as session:
                                    await MediaCacheRepository(session).delete(cached_item.id)
                                cached_parts.pop(identity)
                    incoming_keys = {part.key for part in prepared.parts}
                    return frozenset(
                        part.key
                        for part in prepared_parts
                        if (part.media_kind, part.key) not in cached_parts
                        and part.key in incoming_keys
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
                    on_prepared,
                )
                package = result.package
                audit_event(
                    "download_backend_completed",
                    telegram_user_id=telegram_user_id,
                    download_id=download_id,
                    source=source,
                    source_media_id=source_media_id,
                    backend=result.backend,
                    artifact_count=len(package.artifacts),
                    failure_count=len(package.failures),
                )
                if not prepared_parts:
                    prepared_parts = tuple(
                        MediaPart(
                            key=artifact.part_key,
                            media_kind=artifact.media_kind,
                            position=index,
                        )
                        for index, artifact in enumerate(package.artifacts)
                    )
                if not package.artifacts and not cached_parts:
                    raise BackendError(
                        "Загрузчик вернул пустой пакет",
                        kind=BackendErrorKind.RETRYABLE,
                    )
                valid_artifacts: list[MediaArtifact] = []
                failures = list(package.failures)
                file_sizes: list[int] = []
                for artifact in package.artifacts:
                    size = await asyncio.to_thread(os.path.getsize, artifact.path)
                    if is_too_large(size, self.settings.max_upload_bytes):
                        failures.append(
                            MediaPartFailure(
                                part_key=artifact.part_key,
                                media_kind=artifact.media_kind,
                                error_kind=BackendErrorKind.TOO_LARGE,
                            )
                        )
                        continue
                    valid_artifacts.append(artifact)
                    file_sizes.append(size)
                file_size = sum(file_sizes)
                available_keys = set(
                    cached_item.requested_option for cached_item in cached_parts.values()
                )
                available_keys.update(artifact.part_key for artifact in valid_artifacts)
                failed_keys = {failure.part_key for failure in failures}
                for part in prepared_parts:
                    if part.key not in available_keys and part.key not in failed_keys:
                        failures.append(
                            MediaPartFailure(
                                part_key=part.key,
                                media_kind=part.media_kind,
                                error_kind=BackendErrorKind.UNSUPPORTED,
                            )
                        )
                if not valid_artifacts and not cached_parts:
                    all_too_large = failures and all(
                        isinstance(failure, MediaPartFailure)
                        and failure.error_kind is BackendErrorKind.TOO_LARGE
                        for failure in failures
                    )
                    await self._status(
                        download_id,
                        DownloadStatus.TOO_LARGE if all_too_large else DownloadStatus.FAILED,
                        file_size=file_size,
                        processing_seconds=time.monotonic() - started,
                    )
                    await reporter.update(
                        (
                            "⚠️ Файл получился слишком большим для отправки в Telegram."
                            if all_too_large
                            else "❌ Не удалось скачать медиа. Попробуйте позже."
                        ),
                        force=True,
                    )
                    audit_event(
                        "download_finished",
                        telegram_user_id=telegram_user_id,
                        download_id=download_id,
                        source=source,
                        source_media_id=source_media_id,
                        backend=result.backend,
                        status=(
                            DownloadStatus.TOO_LARGE if all_too_large else DownloadStatus.FAILED
                        ),
                        outcome="no_deliverable_artifacts",
                        failed_part_count=len(failures),
                        total_bytes=file_size,
                        duration_ms=round((time.monotonic() - started) * 1000),
                    )
                    return
                await self._status(download_id, DownloadStatus.UPLOADING, file_size=file_size)
                await reporter.update("📤 Отправляю в Telegram...", force=True)
                artifacts_by_key = {artifact.part_key: artifact for artifact in valid_artifacts}
                upload_results, delivery_failures = await self._deliver_parts(
                    bot=bot,
                    chat_id=chat_id,
                    parts=prepared_parts,
                    cached=cached_parts,
                    artifacts=artifacts_by_key,
                    caption=format_video_caption(
                        title=title,
                        source_url=source_url,
                        height=(valid_artifacts[0].height if valid_artifacts else None),
                        bot_username=self.bot_username,
                    ),
                    continue_on_error=True,
                )
                failures.extend(delivery_failures)
                for upload in upload_results:
                    audit_event(
                        "download_part_sent",
                        telegram_user_id=telegram_user_id,
                        chat_id=chat_id,
                        download_id=download_id,
                        part_key=upload.part_key,
                        media_kind=upload.media_kind,
                        cache_hit=(upload.media_kind, upload.part_key) in cached_parts,
                        sent_as_document=upload.sent_as_document,
                    )
                if not upload_results:
                    await self._status(
                        download_id,
                        DownloadStatus.FAILED,
                        error_type="telegram_api",
                        processing_seconds=time.monotonic() - started,
                    )
                    await reporter.update("❌ Не удалось отправить файл в Telegram.", force=True)
                    audit_event(
                        "download_finished",
                        level=logging.ERROR,
                        telegram_user_id=telegram_user_id,
                        download_id=download_id,
                        source=source,
                        source_media_id=source_media_id,
                        backend=result.backend,
                        status=DownloadStatus.FAILED,
                        outcome="telegram_delivery_failed",
                        duration_ms=round((time.monotonic() - started) * 1000),
                    )
                    return
                new_results = [
                    upload
                    for upload in upload_results
                    if (upload.media_kind, upload.part_key) not in cached_parts
                ]
                elapsed = time.monotonic() - started
                async with self.database.session() as session:
                    cache_repository = MediaCacheRepository(session)
                    for upload in new_results:
                        artifact = artifacts_by_key[upload.part_key]
                        await cache_repository.store(
                            source=source,
                            source_media_id=source_media_id,
                            media_kind=upload.media_kind,
                            requested_option=(
                                option_key
                                if source in QUALITY_SELECTION_SOURCES
                                else upload.part_key
                            ),
                            telegram_file_id=upload.file_id,
                            telegram_file_unique_id=upload.file_unique_id,
                            sent_as_document=upload.sent_as_document,
                            actual_quality=artifact.height,
                            file_size=await asyncio.to_thread(os.path.getsize, artifact.path),
                        )
                    primary = valid_artifacts[0] if valid_artifacts else None
                    await DownloadRepository(session).set_status(
                        download_id,
                        DownloadStatus.COMPLETED,
                        actual_quality=primary.height if primary else None,
                        file_size=file_size,
                        processing_seconds=elapsed,
                        backend=result.backend,
                    )
                    await UserRepository(session).increment_downloads(user_id)
                audit_event(
                    "download_finished",
                    telegram_user_id=telegram_user_id,
                    chat_id=chat_id,
                    download_id=download_id,
                    source=source,
                    source_media_id=source_media_id,
                    media_kind=media_kind,
                    option=option_key,
                    title=title,
                    backend=result.backend,
                    status=DownloadStatus.COMPLETED,
                    outcome="partial" if failures else "success",
                    file_count=len(upload_results),
                    cached_part_count=len(cached_parts),
                    failed_part_count=len(failures),
                    total_bytes=file_size,
                    duration_ms=round(elapsed * 1000),
                )
                if failures:
                    unavailable = ", ".join(
                        dict.fromkeys(
                            {
                                MediaKind.VIDEO: "видео",
                                MediaKind.PHOTO: "фото",
                                MediaKind.ANIMATION: "анимация",
                                MediaKind.AUDIO: "аудио",
                            }[failure.media_kind]
                            for failure in failures
                        )
                    )
                    await reporter.update(
                        f"⚠️ Публикация отправлена частично. Недоступно: {unavailable}.",
                        force=True,
                    )
                else:
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
            audit_event(
                "download_finished",
                level=logging.ERROR,
                telegram_user_id=telegram_user_id,
                download_id=download_id,
                source=source,
                source_media_id=source_media_id,
                status=DownloadStatus.TIMEOUT,
                outcome="timeout",
                duration_ms=round((time.monotonic() - started) * 1000),
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
            audit_event(
                "download_finished",
                telegram_user_id=telegram_user_id,
                download_id=download_id,
                source=source,
                source_media_id=source_media_id,
                status=DownloadStatus.CANCELLED,
                outcome="shutdown",
                duration_ms=round((time.monotonic() - started) * 1000),
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
                audit_event(
                    "download_finished",
                    level=logging.ERROR,
                    telegram_user_id=telegram_user_id,
                    download_id=download_id,
                    source=source,
                    source_media_id=source_media_id,
                    status=DownloadStatus.TOO_LARGE,
                    outcome=error.kind,
                    duration_ms=round((time.monotonic() - started) * 1000),
                )
                return
            await self._status(
                download_id,
                DownloadStatus.FAILED,
                error_type=f"backend_{error.kind.value}",
                processing_seconds=time.monotonic() - started,
            )
            await reporter.update("❌ Не удалось скачать видео. Попробуйте позже.", force=True)
            audit_event(
                "download_finished",
                level=logging.ERROR,
                telegram_user_id=telegram_user_id,
                download_id=download_id,
                source=source,
                source_media_id=source_media_id,
                status=DownloadStatus.FAILED,
                outcome=f"backend_{error.kind.value}",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except TelegramAPIError:
            logger.exception("Telegram upload failed for download_id=%s", download_id)
            await self._status(
                download_id,
                DownloadStatus.FAILED,
                error_type="telegram_api",
                processing_seconds=time.monotonic() - started,
            )
            await reporter.update("❌ Не удалось отправить файл в Telegram.", force=True)
            audit_event(
                "download_finished",
                level=logging.ERROR,
                telegram_user_id=telegram_user_id,
                download_id=download_id,
                source=source,
                source_media_id=source_media_id,
                status=DownloadStatus.FAILED,
                outcome="telegram_api",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        except Exception:
            logger.exception("Unexpected download failure for download_id=%s", download_id)
            await self._status(
                download_id,
                DownloadStatus.FAILED,
                error_type="internal",
                processing_seconds=time.monotonic() - started,
            )
            await reporter.update("❌ Не удалось скачать видео. Попробуйте позже.", force=True)
            audit_event(
                "download_finished",
                level=logging.ERROR,
                telegram_user_id=telegram_user_id,
                download_id=download_id,
                source=source,
                source_media_id=source_media_id,
                status=DownloadStatus.FAILED,
                outcome="internal",
                duration_ms=round((time.monotonic() - started) * 1000),
            )
        finally:
            if job_dir is not None:
                await asyncio.shield(self.storage.remove_job(job_dir))

    async def _deliver_parts(
        self,
        *,
        bot: Bot,
        chat_id: int,
        parts: tuple[MediaPart, ...],
        cached: dict[tuple[MediaKind, str], object],
        artifacts: dict[str, MediaArtifact],
        caption: str,
        continue_on_error: bool = False,
    ) -> tuple[tuple[UploadResult, ...], tuple[MediaPartFailure, ...]]:
        results: list[UploadResult] = []
        failures: list[MediaPartFailure] = []
        ordered = tuple(sorted(parts, key=lambda part: part.position))
        visual_inputs: list[TelegramMediaInput] = []
        for part in ordered:
            if part.media_kind not in {MediaKind.PHOTO, MediaKind.VIDEO}:
                continue
            cached_item = cached.get((part.media_kind, part.key))
            if cached_item is not None:
                visual_inputs.append(
                    TelegramMediaInput(
                        part_key=part.key,
                        media=cached_item.telegram_file_id,  # type: ignore[attr-defined]
                        media_kind=part.media_kind,
                        cached=True,
                    )
                )
            elif artifact := artifacts.get(part.key):
                visual_inputs.append(
                    TelegramMediaInput(
                        part_key=part.key,
                        media=artifact.path.as_uri(),
                        media_kind=part.media_kind,
                        duration=artifact.duration,
                        width=artifact.width,
                        height=artifact.height,
                    )
                )
        caption_available = bool(caption)
        for start in range(0, len(visual_inputs), 10):
            batch = visual_inputs[start : start + 10]
            try:
                if len(batch) == 1:
                    item = batch[0]
                    cached_item = cached.get((item.media_kind, item.part_key))
                    if cached_item is not None:
                        results.append(
                            await self.telegram_files.send_cached(
                                bot,
                                chat_id=chat_id,
                                part_key=item.part_key,
                                media_kind=item.media_kind,
                                file_id=cached_item.telegram_file_id,  # type: ignore[attr-defined]
                                sent_as_document=cached_item.sent_as_document,  # type: ignore[attr-defined]
                                caption=caption if caption_available else "",
                            )
                        )
                    elif item.part_key in artifacts:
                        results.extend(
                            await self.telegram_files.send_artifacts(
                                bot,
                                chat_id=chat_id,
                                package=MediaPackage(artifacts=(artifacts[item.part_key],)),
                                caption=caption if caption_available else "",
                            )
                        )
                else:
                    results.extend(
                        await self.telegram_files.send_media_album(
                            bot,
                            chat_id=chat_id,
                            media=batch,
                            caption=caption if caption_available else "",
                        )
                    )
                caption_available = False
            except TelegramAPIError:
                if not continue_on_error:
                    raise
                failures.extend(
                    MediaPartFailure(item.part_key, item.media_kind, BackendErrorKind.RETRYABLE)
                    for item in batch
                )
        for part in ordered:
            if part.media_kind not in {MediaKind.ANIMATION, MediaKind.AUDIO}:
                continue
            cached_item = cached.get((part.media_kind, part.key))
            try:
                if cached_item is not None:
                    results.append(
                        await self.telegram_files.send_cached(
                            bot,
                            chat_id=chat_id,
                            part_key=part.key,
                            media_kind=part.media_kind,
                            file_id=cached_item.telegram_file_id,  # type: ignore[attr-defined]
                            sent_as_document=cached_item.sent_as_document,  # type: ignore[attr-defined]
                            caption=caption if caption_available else "",
                        )
                    )
                elif part.key in artifacts:
                    results.extend(
                        await self.telegram_files.send_artifacts(
                            bot,
                            chat_id=chat_id,
                            package=MediaPackage(artifacts=(artifacts[part.key],)),
                            caption=caption if caption_available else "",
                        )
                    )
                caption_available = False
            except TelegramAPIError:
                if not continue_on_error:
                    raise
                failures.append(
                    MediaPartFailure(part.key, part.media_kind, BackendErrorKind.RETRYABLE)
                )
        return tuple(results), tuple(failures)

    async def _status(
        self,
        download_id: int,
        status: DownloadStatus,
        **values: object,
    ) -> None:
        async with self.database.session() as session:
            await DownloadRepository(session).set_status(download_id, status, **values)  # type: ignore[arg-type]
        audit_event("download_status_changed", download_id=download_id, status=status, **values)
