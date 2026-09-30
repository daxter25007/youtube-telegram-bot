from pathlib import Path
from types import SimpleNamespace

import pytest

from app.database.base import Base
from app.database.models import DownloadStatus, User
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.media_cache import MediaCacheRepository
from app.database.repositories.media_manifests import MediaManifestRepository
from app.database.session import Database
from app.services.cleanup import TempStorage
from app.services.download_manager import DownloadManager
from app.services.media.backends.base import (
    BackendErrorKind,
    BackendResult,
    DownloadBackend,
    MediaPartFailure,
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


class FakeSubscriptions:
    async def missing_channels(self, bot, user_id, channels):
        return []


def uploaded(part_key: str, kind: MediaKind, *, document: bool = False) -> UploadResult:
    file = SimpleNamespace(file_id=f"file-{part_key}", file_unique_id=f"unique-{part_key}")
    message = SimpleNamespace(video=None, audio=None, photo=None, document=None)
    if document:
        message.document = file
    elif kind is MediaKind.VIDEO:
        message.video = file
    elif kind is MediaKind.AUDIO:
        message.audio = file
    else:
        message.photo = [file]
    return UploadResult(
        part_key=part_key,
        media_kind=kind,
        message=message,
        sent_as_document=document,
    )


class RecordingTelegramFiles:
    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send_cached(self, bot, *, part_key, media_kind, sent_as_document, **kwargs):
        self.sent.append(part_key)
        return uploaded(part_key, media_kind, document=sent_as_document)

    async def send_artifacts(self, bot, *, package, **kwargs):
        results = []
        for artifact in package.artifacts:
            self.sent.append(artifact.part_key)
            results.append(uploaded(artifact.part_key, artifact.media_kind))
        return tuple(results)

    async def send_photo_album(self, bot, *, photos, **kwargs):
        results = []
        for photo in photos:
            self.sent.append(photo.part_key)
            results.append(uploaded(photo.part_key, MediaKind.PHOTO))
        return tuple(results)


class InvalidPhotoTelegramFiles(RecordingTelegramFiles):
    async def send_photo_album(self, bot, *, photos, **kwargs):
        raise InvalidCachedPartError("photo_001")


class RecordingRouter:
    def __init__(
        self,
        parts: tuple[MediaPart, ...],
        failures: tuple[MediaPartFailure, ...] = (),
    ) -> None:
        self.parts = parts
        self.failures = failures
        self.requested: frozenset[str] = frozenset()
        self.calls = 0

    async def download(self, request, job_dir, progress, on_fallback, on_prepared):
        self.calls += 1
        prepared = PreparedMedia(request=request, parts=self.parts)
        self.requested = await on_prepared(prepared)
        artifacts = []
        failed_keys = {failure.part_key for failure in self.failures}
        for part in self.parts:
            if part.key not in self.requested or part.key in failed_keys:
                continue
            suffix = {MediaKind.VIDEO: ".mp4", MediaKind.PHOTO: ".jpg", MediaKind.AUDIO: ".m4a"}[
                part.media_kind
            ]
            path = job_dir / f"{part.key}{suffix}"
            path.write_bytes(part.key.encode())
            artifacts.append(
                MediaArtifact(path=path, media_kind=part.media_kind, part_key=part.key)
            )
        return BackendResult(
            package=MediaPackage(artifacts=tuple(artifacts), failures=self.failures),
            backend=DownloadBackend.COBALT,
        )


async def setup_database(tmp_path: Path):
    database = Database(f"sqlite+aiosqlite:///{tmp_path / 'bundle.db'}")
    async with database.engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    async with database.session() as session:
        user = User(telegram_id=123, first_name="Test")
        session.add(user)
        await session.flush()
        download = await DownloadRepository(session).create(
            user_id=user.id,
            source=MediaSource.TIKTOK,
            media_kind=MediaKind.VIDEO,
            source_media_id="post-1",
            source_url="https://vm.tiktok.com/POST1/",
            video_title="TikTok post",
            requested_option="tiktok_bundle",
            requested_quality=None,
            status=DownloadStatus.QUEUED,
        )
        return database, user.id, download.id


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        progress_update_seconds=0,
        download_timeout_seconds=5,
        max_upload_bytes=1_000,
    )


def manager(database, tmp_path, router, telegram):
    return DownloadManager(
        database=database,
        settings=settings(),  # type: ignore[arg-type]
        storage=TempStorage(tmp_path / "jobs"),
        backend_router=router,  # type: ignore[arg-type]
        subscriptions=FakeSubscriptions(),  # type: ignore[arg-type]
        telegram_files=telegram,  # type: ignore[arg-type]
        bot_username="MyVideoBot",
    )


async def store_cache(session, part: MediaPart) -> None:
    await MediaCacheRepository(session).store(
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
        media_kind=part.media_kind,
        requested_option=part.key,
        telegram_file_id=f"cached-{part.key}",
        telegram_file_unique_id=f"unique-{part.key}",
        sent_as_document=False,
        actual_quality=None,
        file_size=10,
    )


@pytest.mark.asyncio
async def test_complete_manifest_cache_delivers_without_backend(tmp_path: Path) -> None:
    parts = (
        MediaPart(key="video", media_kind=MediaKind.VIDEO, position=0),
        MediaPart(key="audio", media_kind=MediaKind.AUDIO, position=1),
    )
    database, user_id, download_id = await setup_database(tmp_path)
    async with database.session() as session:
        await MediaManifestRepository(session).upsert(
            source=MediaSource.TIKTOK, source_media_id="post-1", parts=parts
        )
        for part in parts:
            await store_cache(session, part)
    telegram = RecordingTelegramFiles()
    router = RecordingRouter(parts)

    delivered = await manager(database, tmp_path, router, telegram).try_cached_publication(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        user_id=user_id,
        download_id=download_id,
        source_media_id="post-1",
        title="TikTok post",
        source_url="https://vm.tiktok.com/POST1/",
        progress_message=FakeMessage(),  # type: ignore[arg-type]
    )

    assert delivered is True
    assert router.calls == 0
    assert telegram.sent == ["video", "audio"]
    await database.close()


@pytest.mark.asyncio
async def test_partial_cache_downloads_only_missing_part_and_preserves_order(
    tmp_path: Path,
) -> None:
    parts = (
        MediaPart(key="video", media_kind=MediaKind.VIDEO, position=0),
        MediaPart(key="audio", media_kind=MediaKind.AUDIO, position=1),
    )
    database, user_id, download_id = await setup_database(tmp_path)
    async with database.session() as session:
        await MediaManifestRepository(session).upsert(
            source=MediaSource.TIKTOK, source_media_id="post-1", parts=parts
        )
        await store_cache(session, parts[0])
    telegram = RecordingTelegramFiles()
    router = RecordingRouter(parts)
    progress = FakeMessage()

    await manager(database, tmp_path, router, telegram).run(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        telegram_user_id=123,
        user_id=user_id,
        download_id=download_id,
        source_url="https://vm.tiktok.com/POST1/",
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
        media_kind=MediaKind.VIDEO,
        option_key="tiktok_bundle",
        policy=DownloadPolicy(format_selector="best"),
        title="TikTok post",
        progress_message=progress,  # type: ignore[arg-type]
    )

    assert router.requested == frozenset({"audio"})
    assert telegram.sent == ["video", "audio"]
    async with database.session() as session:
        assert await MediaCacheRepository(session).get(
            source=MediaSource.TIKTOK,
            source_media_id="post-1",
            media_kind=MediaKind.AUDIO,
            requested_option="audio",
        )
    await database.close()


@pytest.mark.asyncio
async def test_invalid_cached_photo_invalidates_only_that_part(tmp_path: Path) -> None:
    parts = (
        MediaPart(key="photo_000", media_kind=MediaKind.PHOTO, position=0),
        MediaPart(key="photo_001", media_kind=MediaKind.PHOTO, position=1),
        MediaPart(key="audio", media_kind=MediaKind.AUDIO, position=2),
    )
    database, user_id, download_id = await setup_database(tmp_path)
    async with database.session() as session:
        await MediaManifestRepository(session).upsert(
            source=MediaSource.TIKTOK, source_media_id="post-1", parts=parts
        )
        for part in parts:
            await store_cache(session, part)

    delivered = await manager(
        database,
        tmp_path,
        RecordingRouter(parts),
        InvalidPhotoTelegramFiles(),
    ).try_cached_publication(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        user_id=user_id,
        download_id=download_id,
        source_media_id="post-1",
        title="TikTok post",
        source_url="https://vm.tiktok.com/POST1/",
        progress_message=FakeMessage(),  # type: ignore[arg-type]
    )

    async with database.session() as session:
        remaining = await MediaCacheRepository(session).list_for_media(
            source=MediaSource.TIKTOK,
            source_media_id="post-1",
        )
    assert delivered is False
    assert {(item.media_kind, item.requested_option) for item in remaining} == {
        (MediaKind.PHOTO, "photo_000"),
        (MediaKind.AUDIO, "audio"),
    }
    await database.close()


@pytest.mark.asyncio
async def test_partial_failure_delivers_survivor_and_completes_with_notice(tmp_path: Path) -> None:
    parts = (
        MediaPart(key="photo_000", media_kind=MediaKind.PHOTO, position=0),
        MediaPart(key="audio", media_kind=MediaKind.AUDIO, position=1),
    )
    failure = MediaPartFailure(
        part_key="photo_000",
        media_kind=MediaKind.PHOTO,
        error_kind=BackendErrorKind.TOO_LARGE,
    )
    database, user_id, download_id = await setup_database(tmp_path)
    telegram = RecordingTelegramFiles()
    progress = FakeMessage()

    await manager(database, tmp_path, RecordingRouter(parts, (failure,)), telegram).run(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        telegram_user_id=123,
        user_id=user_id,
        download_id=download_id,
        source_url="https://vm.tiktok.com/POST1/",
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
        media_kind=MediaKind.VIDEO,
        option_key="tiktok_bundle",
        policy=DownloadPolicy(format_selector="best"),
        title="TikTok post",
        progress_message=progress,  # type: ignore[arg-type]
    )

    async with database.session() as session:
        download = await DownloadRepository(session).get(download_id)
    assert download is not None and download.status is DownloadStatus.COMPLETED
    assert telegram.sent == ["audio"]
    assert any("частично" in text.casefold() for text in progress.texts)
    await database.close()
