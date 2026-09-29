import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import DownloadStatus, User
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.requests import DownloadRequestRepository
from app.services.media.backends.base import DownloadBackend
from app.services.media.models import MediaKind, MediaSource


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
async def test_download_repository_persists_generic_media_fields(session) -> None:
    user = User(telegram_id=100, first_name="Test")
    session.add(user)
    await session.flush()

    download = await DownloadRepository(session).create(
        user_id=user.id,
        source=MediaSource.TIKTOK,
        media_kind=MediaKind.VIDEO,
        source_media_id="7461234567890123456",
        source_url="https://www.tiktok.com/@creator/video/7461234567890123456",
        video_title="TikTok",
        requested_option="best_video",
        requested_quality=None,
        status=DownloadStatus.QUEUED,
    )

    assert download.source is MediaSource.TIKTOK
    assert download.media_kind is MediaKind.VIDEO
    assert download.source_media_id == "7461234567890123456"
    assert download.requested_option == "best_video"
    assert download.requested_quality is None

    await DownloadRepository(session).set_status(
        download.id,
        DownloadStatus.COMPLETED,
        backend=DownloadBackend.COBALT,
    )
    assert download.backend is DownloadBackend.COBALT


@pytest.mark.asyncio
async def test_request_repository_persists_generic_media_fields_and_ownership(session) -> None:
    user = User(telegram_id=200, first_name="Test")
    session.add(user)
    await session.flush()

    request = await DownloadRequestRepository(session).create(
        user_id=user.id,
        telegram_user_id=200,
        source=MediaSource.TIKTOK,
        media_kind=MediaKind.VIDEO,
        source_url="https://vm.tiktok.com/ZM123abc/",
        source_media_id="7461234567890123456",
        metadata={"title": "TikTok", "options": []},
    )
    await session.commit()

    assert request.source is MediaSource.TIKTOK
    assert request.media_kind is MediaKind.VIDEO
    assert request.source_url == "https://vm.tiktok.com/ZM123abc/"
    assert await DownloadRequestRepository(session).get_owned_valid(request.id, 200, 30)
    assert await DownloadRequestRepository(session).get_owned_valid(request.id, 201, 30) is None
