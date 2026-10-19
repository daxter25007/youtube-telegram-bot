from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import DownloadStatus, User
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.requests import DownloadRequestRepository
from app.services.media.models import MediaKind, MediaPart, MediaSource


@pytest.fixture
async def session():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as value:
        yield value
    await engine.dispose()


@pytest.mark.asyncio
async def test_repositories_and_request_ownership_ttl(session) -> None:
    user = User(telegram_id=100, first_name="Test")
    session.add(user)
    await session.flush()
    request_repo = DownloadRequestRepository(session)
    request = await request_repo.create(
        user_id=user.id,
        telegram_user_id=100,
        source=MediaSource.YOUTUBE,
        media_kind=MediaKind.VIDEO,
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        source_media_id="dQw4w9WgXcQ",
        metadata={"title": "test", "options": []},
    )
    await session.commit()
    assert await request_repo.get_owned_valid(request.id, 100, 30) is not None
    assert await request_repo.get_owned_valid(request.id, 999, 30) is None
    future = datetime.now(UTC) + timedelta(minutes=31)
    assert await request_repo.get_owned_valid(request.id, 100, 30, now=future) is None

    download = await DownloadRepository(session).create(
        user_id=user.id,
        source=MediaSource.YOUTUBE,
        media_kind=MediaKind.VIDEO,
        source_media_id="dQw4w9WgXcQ",
        source_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_title="test",
        requested_option="video_720",
        requested_quality=720,
        status=DownloadStatus.QUEUED,
    )
    created_download = await DownloadRepository(session).create(
        user_id=user.id,
        source=MediaSource.YOUTUBE,
        media_kind=MediaKind.VIDEO,
        source_media_id="created-video",
        source_url="https://youtu.be/created-video",
        video_title="created",
        requested_option="video_360",
        requested_quality=360,
    )
    assert await DownloadRepository(session).get(download.id) is download
    assert await DownloadRepository(session).cancel_incomplete_after_restart() == 2
    assert download.status == DownloadStatus.CANCELLED
    assert created_download.status == DownloadStatus.CANCELLED


@pytest.mark.asyncio
async def test_media_cache_upserts_and_lists_cached_options(session) -> None:
    from app.database.repositories.media_cache import MediaCacheRepository

    repository = MediaCacheRepository(session)
    await repository.store(
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        requested_option="video_720",
        telegram_file_id="first-file-id",
        telegram_file_unique_id="stable-id",
        sent_as_document=False,
        actual_quality=720,
        file_size=1234,
    )
    await repository.store(
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        requested_option="video_720",
        telegram_file_id="new-file-id",
        telegram_file_unique_id="stable-id",
        sent_as_document=False,
        actual_quality=720,
        file_size=1234,
    )

    cached = await repository.get(
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        requested_option="video_720",
    )

    assert cached is not None
    assert cached.telegram_file_id == "new-file-id"
    assert await repository.cached_option_keys(
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
    ) == {"video_720"}
    assert (
        await repository.cached_option_keys(
            source=MediaSource.YOUTUBE,
            source_media_id="another-video",
            media_kind=MediaKind.VIDEO,
        )
        == set()
    )


@pytest.mark.asyncio
async def test_download_records_cache_hit(session) -> None:
    user = User(telegram_id=200, first_name="Cached")
    session.add(user)
    await session.flush()

    download = await DownloadRepository(session).create(
        user_id=user.id,
        source=MediaSource.YOUTUBE,
        media_kind=MediaKind.VIDEO,
        source_media_id="cached-video",
        source_url="https://youtu.be/cached-video",
        video_title="cached",
        requested_option="video_720",
        requested_quality=720,
        cache_hit=True,
        status=DownloadStatus.COMPLETED,
    )

    assert download.cache_hit is True


@pytest.mark.asyncio
async def test_media_manifest_upserts_ordered_parts(session) -> None:
    from app.database.repositories.media_manifests import MediaManifestRepository

    repository = MediaManifestRepository(session)
    first = (
        MediaPart(key="photo_000", media_kind=MediaKind.PHOTO, position=0),
        MediaPart(key="audio", media_kind=MediaKind.AUDIO, position=1, filename="sound.opus"),
    )
    await repository.upsert(
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
        parts=first,
    )
    replacement = (
        MediaPart(key="video", media_kind=MediaKind.VIDEO, position=0),
        MediaPart(key="audio", media_kind=MediaKind.AUDIO, position=1, filename="sound.m4a"),
    )
    await repository.upsert(
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
        parts=replacement,
    )

    manifest = await repository.get(
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
    )

    assert manifest is not None
    assert manifest.parts == replacement


@pytest.mark.asyncio
async def test_media_cache_lists_and_selects_mixed_part_identities(session) -> None:
    from app.database.repositories.media_cache import MediaCacheRepository

    repository = MediaCacheRepository(session)
    for kind, key in (
        (MediaKind.VIDEO, "video"),
        (MediaKind.PHOTO, "photo_000"),
        (MediaKind.AUDIO, "audio"),
    ):
        await repository.store(
            source=MediaSource.TIKTOK,
            source_media_id="post-1",
            media_kind=kind,
            requested_option=key,
            telegram_file_id=f"file-{key}",
            telegram_file_unique_id=f"unique-{key}",
            sent_as_document=kind is MediaKind.AUDIO,
            actual_quality=None,
            file_size=10,
        )

    listed = await repository.list_for_media(
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
    )
    selected = await repository.get_many(
        source=MediaSource.TIKTOK,
        source_media_id="post-1",
        identities={(MediaKind.PHOTO, "photo_000"), (MediaKind.AUDIO, "audio")},
    )

    assert {(item.media_kind, item.requested_option) for item in listed} == {
        (MediaKind.VIDEO, "video"),
        (MediaKind.PHOTO, "photo_000"),
        (MediaKind.AUDIO, "audio"),
    }
    assert set(selected) == {
        (MediaKind.PHOTO, "photo_000"),
        (MediaKind.AUDIO, "audio"),
    }
