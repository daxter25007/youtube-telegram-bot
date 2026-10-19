from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramBadRequest, TelegramNetworkError
from aiogram.methods import SendMessage

from app.database.base import Base
from app.database.models import DownloadStatus, User
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.media_cache import MediaCacheRepository
from app.database.session import Database
from app.services.cleanup import TempStorage
from app.services.download_manager import DownloadManager, format_video_caption
from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    BackendResult,
    DownloadBackend,
    PreparedMedia,
)
from app.services.media.models import (
    DownloadPolicy,
    MediaArtifact,
    MediaKind,
    MediaPackage,
    MediaPart,
    MediaSource,
)
from app.services.telegram_files import InvalidCachedPartError, UploadResult


class FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def edit_text(self, text: str) -> None:
        self.texts.append(text)

    async def delete(self) -> None:
        self.texts.append("__deleted__")


class FakeTelegramFiles:
    async def send_artifacts(self, bot, **kwargs):
        artifact = kwargs["package"].artifacts[0]
        return (
            UploadResult(
                part_key=artifact.part_key,
                media_kind=artifact.media_kind,
                message=SimpleNamespace(
                    video=SimpleNamespace(
                        file_id="telegram-file-id",
                        file_unique_id="telegram-unique-id",
                    ),
                    document=None,
                ),
                sent_as_document=False,
            ),
        )


class CachingTelegramFiles:
    async def send_artifacts(self, bot, **kwargs):
        artifact = kwargs["package"].artifacts[0]
        return (
            UploadResult(
                part_key=artifact.part_key,
                media_kind=artifact.media_kind,
                message=SimpleNamespace(
                    video=SimpleNamespace(
                        file_id="telegram-file-id",
                        file_unique_id="telegram-unique-id",
                    ),
                    document=None,
                ),
                sent_as_document=False,
            ),
        )


class CachedDeliveryTelegramFiles:
    def __init__(self) -> None:
        self.file_id: str | None = None
        self.sent_as_document: bool | None = None

    async def send_cached(self, bot, *, file_id, sent_as_document, **kwargs):
        self.file_id = file_id
        self.sent_as_document = sent_as_document
        return SimpleNamespace()


class InvalidCachedDeliveryTelegramFiles:
    async def send_cached(self, bot, **kwargs):
        raise InvalidCachedPartError(kwargs["part_key"])


class UnrelatedBadRequestCachedDeliveryTelegramFiles:
    async def send_cached(self, bot, **kwargs):
        raise TelegramBadRequest(
            method=SendMessage(chat_id=1, text="cached"),
            message="chat not found",
        )


class FailingTelegramFiles:
    async def send_artifacts(self, bot, **kwargs):
        raise TelegramNetworkError(
            method=SendMessage(chat_id=1, text="upload"),
            message="network error",
        )


class SuccessfulDownloader:
    async def download(self, request, job_dir: Path, progress, on_fallback, on_prepared):
        await on_prepared(
            PreparedMedia(
                request=request,
                parts=(
                    MediaPart(
                        key=request.option_key,
                        media_kind=request.policy.media_kind,
                        position=0,
                    ),
                ),
            )
        )
        path = job_dir / "media.mp4"
        path.write_bytes(b"video")
        await progress("downloading", 50)
        return BackendResult(
            package=MediaPackage(
                artifacts=(
                    MediaArtifact(
                        path=path,
                        media_kind=MediaKind.VIDEO,
                        part_key=request.option_key,
                        width=10,
                        height=360,
                        duration=1,
                    ),
                )
            ),
            backend=DownloadBackend.YTDLP,
        )


class OversizedDownloader:
    async def download(self, request, job_dir: Path, progress, on_fallback, on_prepared):
        await on_prepared(
            PreparedMedia(
                request=request,
                parts=(
                    MediaPart(
                        key=request.option_key,
                        media_kind=request.policy.media_kind,
                        position=0,
                    ),
                ),
            )
        )
        path = job_dir / "media.mp4"
        path.write_bytes(b"x" * 1_001)
        return BackendResult(
            package=MediaPackage(
                artifacts=(
                    MediaArtifact(
                        path=path,
                        media_kind=MediaKind.VIDEO,
                        part_key=request.option_key,
                        height=720,
                    ),
                )
            ),
            backend=DownloadBackend.YTDLP,
        )


class FailingDownloader:
    async def download(self, *args, **kwargs):
        raise BackendError("failure", kind=BackendErrorKind.RETRYABLE)


class TimeoutDownloader:
    async def download(self, *args, **kwargs):
        await asyncio.sleep(1)
        raise AssertionError


class FallbackDownloader(SuccessfulDownloader):
    async def download(self, request, job_dir: Path, progress, on_fallback, on_prepared):
        await on_fallback(DownloadBackend.COBALT, DownloadBackend.YTDLP)
        return await super().download(request, job_dir, progress, on_fallback, on_prepared)


async def make_database(tmp_path: Path) -> tuple[Database, int, int]:
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'test.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        user = User(telegram_id=123, first_name="Test")
        session.add(user)
        await session.flush()
        download = await DownloadRepository(session).create(
            user_id=user.id,
            source=MediaSource.YOUTUBE,
            media_kind=MediaKind.VIDEO,
            source_media_id="dQw4w9WgXcQ",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_title="test",
            requested_option="video_360",
            requested_quality=360,
            status=DownloadStatus.QUEUED,
        )
        return database, user.id, download.id


def settings(tmp_path: Path, timeout: float = 5) -> SimpleNamespace:
    return SimpleNamespace(
        progress_update_seconds=0,
        download_timeout_seconds=timeout,
        max_upload_bytes=1000,
        temp_root=tmp_path,
    )


async def run_case(
    tmp_path: Path,
    downloader,
    job_timeout: float = 5,
    telegram_files=None,
):
    database, user_id, download_id = await make_database(tmp_path)
    storage = TempStorage(tmp_path / "jobs")
    manager = DownloadManager(
        database=database,
        settings=settings(tmp_path, job_timeout),  # type: ignore[arg-type]
        storage=storage,
        backend_router=downloader,
        telegram_files=telegram_files or FakeTelegramFiles(),  # type: ignore[arg-type]
        bot_username="MyVideoBot",
    )
    message = FakeMessage()
    await manager.run(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        telegram_user_id=123,
        user_id=user_id,
        download_id=download_id,
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        option_key="video_360",
        policy=DownloadPolicy(format_selector="best[height<=360]"),
        title="test",
        progress_message=message,  # type: ignore[arg-type]
    )
    async with database.session() as session:
        status = (await DownloadRepository(session).get(download_id)).status
    jobs = list((tmp_path / "jobs").iterdir())
    await database.close()
    return status, jobs, message.texts


@pytest.mark.asyncio
async def test_successful_cleanup(tmp_path: Path) -> None:
    status, jobs, texts = await run_case(tmp_path, SuccessfulDownloader())
    assert status == DownloadStatus.COMPLETED
    assert jobs == []
    assert texts[-1] == "__deleted__"


@pytest.mark.asyncio
async def test_successful_download_is_saved_to_media_cache(tmp_path: Path) -> None:
    database, user_id, download_id = await make_database(tmp_path)
    manager = DownloadManager(
        database=database,
        settings=settings(tmp_path),  # type: ignore[arg-type]
        storage=TempStorage(tmp_path / "jobs"),
        backend_router=SuccessfulDownloader(),  # type: ignore[arg-type]
        telegram_files=CachingTelegramFiles(),  # type: ignore[arg-type]
        bot_username="MyVideoBot",
    )

    await manager.run(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        telegram_user_id=123,
        user_id=user_id,
        download_id=download_id,
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        option_key="video_360",
        policy=DownloadPolicy(format_selector="best[height<=360]"),
        title="test",
        progress_message=FakeMessage(),  # type: ignore[arg-type]
    )

    async with database.session() as session:
        cached = await MediaCacheRepository(session).get(
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            requested_option="video_360",
        )
    await database.close()

    assert cached is not None
    assert cached.telegram_file_id == "telegram-file-id"
    assert cached.actual_quality == 360


@pytest.mark.asyncio
async def test_cached_download_is_delivered_and_recorded_without_backend(tmp_path: Path) -> None:
    database, user_id, download_id = await make_database(tmp_path)
    async with database.session() as session:
        cached = await MediaCacheRepository(session).store(
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            requested_option="video_360",
            telegram_file_id="cached-file-id",
            telegram_file_unique_id="cached-unique-id",
            sent_as_document=False,
            actual_quality=360,
            file_size=1234,
        )
        cached.last_used_at = datetime(2000, 1, 1, tzinfo=UTC)
    telegram_files = CachedDeliveryTelegramFiles()
    manager = DownloadManager(
        database=database,
        settings=settings(tmp_path),  # type: ignore[arg-type]
        storage=TempStorage(tmp_path / "jobs"),
        backend_router=object(),  # type: ignore[arg-type]
        telegram_files=telegram_files,  # type: ignore[arg-type]
        bot_username="MyVideoBot",
    )
    message = FakeMessage()

    delivered = await manager.try_cached(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        user_id=user_id,
        download_id=download_id,
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        option_key="video_360",
        title="test",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        progress_message=message,  # type: ignore[arg-type]
    )

    async with database.session() as session:
        download = await DownloadRepository(session).get(download_id)
        user = await session.get(User, user_id)
        cached = await MediaCacheRepository(session).get(
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            requested_option="video_360",
        )
    await database.close()

    assert delivered is True
    assert telegram_files.file_id == "cached-file-id"
    assert download is not None
    assert download.status is DownloadStatus.COMPLETED
    assert download.cache_hit is True
    assert user is not None and user.downloads_count == 1
    assert cached is not None and cached.last_used_at.year > 2000
    assert message.texts[-1] == "__deleted__"


@pytest.mark.asyncio
async def test_invalid_cached_file_is_removed_and_allows_normal_download(tmp_path: Path) -> None:
    database, user_id, download_id = await make_database(tmp_path)
    async with database.session() as session:
        await MediaCacheRepository(session).store(
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            requested_option="video_360",
            telegram_file_id="invalid-file-id",
            telegram_file_unique_id="invalid-unique-id",
            sent_as_document=False,
            actual_quality=360,
            file_size=1234,
        )
    manager = DownloadManager(
        database=database,
        settings=settings(tmp_path),  # type: ignore[arg-type]
        storage=TempStorage(tmp_path / "jobs"),
        backend_router=object(),  # type: ignore[arg-type]
        telegram_files=InvalidCachedDeliveryTelegramFiles(),  # type: ignore[arg-type]
        bot_username="MyVideoBot",
    )

    delivered = await manager.try_cached(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        user_id=user_id,
        download_id=download_id,
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        option_key="video_360",
        title="test",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        progress_message=FakeMessage(),  # type: ignore[arg-type]
    )

    async with database.session() as session:
        cached = await MediaCacheRepository(session).get(
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            requested_option="video_360",
        )
    await database.close()

    assert delivered is False
    assert cached is None


@pytest.mark.asyncio
async def test_unrelated_bad_request_keeps_valid_cached_file(tmp_path: Path) -> None:
    database, user_id, download_id = await make_database(tmp_path)
    async with database.session() as session:
        await MediaCacheRepository(session).store(
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            requested_option="video_360",
            telegram_file_id="valid-file-id",
            telegram_file_unique_id="valid-unique-id",
            sent_as_document=False,
            actual_quality=360,
            file_size=1234,
        )
    manager = DownloadManager(
        database=database,
        settings=settings(tmp_path),  # type: ignore[arg-type]
        storage=TempStorage(tmp_path / "jobs"),
        backend_router=object(),  # type: ignore[arg-type]
        telegram_files=UnrelatedBadRequestCachedDeliveryTelegramFiles(),  # type: ignore[arg-type]
        bot_username="MyVideoBot",
    )

    with pytest.raises(TelegramBadRequest):
        await manager.try_cached(
            bot=object(),  # type: ignore[arg-type]
            chat_id=123,
            user_id=user_id,
            download_id=download_id,
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            option_key="video_360",
            title="test",
            source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            progress_message=FakeMessage(),  # type: ignore[arg-type]
        )

    async with database.session() as session:
        cached = await MediaCacheRepository(session).get(
            source=MediaSource.YOUTUBE,
            source_media_id="dQw4w9WgXcQ",
            media_kind=MediaKind.VIDEO,
            requested_option="video_360",
        )
    await database.close()

    assert cached is not None


def test_video_caption_matches_compact_youtube_template_and_escapes_html() -> None:
    assert format_video_caption(
        title="Cats & <Dogs>",
        source_url="https://youtu.be/abc?x=1&y=2",
        height=480,
        bot_username="@MyVideoBot",
    ) == (
        "🍿 <code>Cats &amp; &lt;Dogs&gt;</code>\n\n"
        '🔗 <a href="https://youtu.be/abc?x=1&amp;y=2">'
        "https://youtu.be/abc?x=1&amp;y=2</a>\n\n"
        "📺 480p\n\n"
        '<a href="https://youtu.be/abc?x=1&amp;y=2">&#8203;</a>💙 @MyVideoBot'
    )


def test_video_caption_uses_same_template_for_tiktok_without_unknown_quality() -> None:
    assert format_video_caption(
        title="TikTok example",
        source_url="https://www.tiktok.com/@creator/video/123",
        height=None,
        bot_username="MyVideoBot",
    ) == (
        "🍿 <code>TikTok example</code>\n\n"
        '🔗 <a href="https://www.tiktok.com/@creator/video/123">'
        "https://www.tiktok.com/@creator/video/123</a>\n\n"
        '<a href="https://www.tiktok.com/@creator/video/123">&#8203;</a>💙 @MyVideoBot'
    )


def test_video_caption_truncates_non_bmp_title_to_telegram_utf16_limit() -> None:
    caption = format_video_caption(
        title="😀" * 512,
        source_url="https://www.youtube.com/watch?v=abc123",
        height=480,
        bot_username="MyVideoBot",
    )

    assert f"<code>{'😀' * 475}…</code>" in caption
    assert "😀" * 476 not in caption


@pytest.mark.asyncio
async def test_cleanup_after_exception(tmp_path: Path) -> None:
    status, jobs, _ = await run_case(tmp_path, FailingDownloader())
    assert status == DownloadStatus.FAILED
    assert jobs == []


@pytest.mark.asyncio
async def test_cleanup_after_timeout(tmp_path: Path) -> None:
    status, jobs, texts = await run_case(tmp_path, TimeoutDownloader(), job_timeout=0.01)
    assert status == DownloadStatus.TIMEOUT
    assert jobs == []
    assert any("слишком много времени" in text for text in texts)


@pytest.mark.asyncio
async def test_cleanup_after_telegram_upload_error(tmp_path: Path) -> None:
    status, jobs, texts = await run_case(
        tmp_path,
        SuccessfulDownloader(),
        telegram_files=FailingTelegramFiles(),
    )
    assert status == DownloadStatus.FAILED
    assert jobs == []
    assert any("отправить файл" in text for text in texts)


@pytest.mark.asyncio
async def test_cleanup_after_actual_file_exceeds_limit(tmp_path: Path) -> None:
    status, jobs, texts = await run_case(tmp_path, OversizedDownloader())
    assert status == DownloadStatus.TOO_LARGE
    assert jobs == []
    assert any("слишком большим" in text for text in texts)


@pytest.mark.asyncio
async def test_manager_hides_cobalt_fallback_from_user(tmp_path: Path, caplog) -> None:
    with caplog.at_level("INFO"):
        status, jobs, texts = await run_case(tmp_path, FallbackDownloader())

    assert status == DownloadStatus.COMPLETED
    assert jobs == []
    assert not any("резервный способ" in text for text in texts)
    assert "Switching media backend" in caplog.text
