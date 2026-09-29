from types import SimpleNamespace

import pytest
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.routers.downloader import receive_url
from app.database.base import Base
from app.database.models import Download, DownloadRequest, User
from app.services.media.models import (
    DownloadOption,
    MediaKind,
    MediaMetadata,
    MediaSource,
)
from app.services.media.registry import MediaRegistry


@pytest.fixture
async def session_and_user():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:")
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        user = User(telegram_id=123, first_name="Test")
        session.add(user)
        await session.flush()
        yield session, user
    await engine.dispose()


class FakeStatusMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []
        self.reply_markup = None
        self.chat = SimpleNamespace(id=123)

    async def edit_text(self, text: str, reply_markup=None):
        self.texts.append(text)
        self.reply_markup = reply_markup
        return self


class FakeIncomingMessage:
    def __init__(self, text: str) -> None:
        self.text = text
        self.from_user = SimpleNamespace(id=123)
        self.answers: list[tuple[str, object | None]] = []
        self.status = FakeStatusMessage()

    async def answer(self, text: str, reply_markup=None):
        self.answers.append((text, reply_markup))
        return self.status


class FakeMetadataService:
    def __init__(self) -> None:
        self.calls = []

    async def fetch(self, parsed):
        self.calls.append(parsed)
        if parsed.source is MediaSource.YOUTUBE:
            options = (
                DownloadOption(
                    key="video_720",
                    label="720p",
                    media_kind=MediaKind.VIDEO,
                    height=720,
                ),
            )
            title = "YouTube example"
        else:
            options = (
                DownloadOption(
                    key="best_video",
                    label="Лучшее качество",
                    media_kind=MediaKind.VIDEO,
                    height=1024,
                ),
            )
            title = "TikTok example"
        return MediaMetadata(
            source=parsed.source,
            media_id=parsed.media_id or "7461234567890123456",
            source_url=parsed.normalized_url,
            title=title,
            duration=15,
            thumbnail=None,
            media_kind=MediaKind.VIDEO,
            options=options,
        )


class FakeRateLimiter:
    async def allow(self, user_id: int) -> bool:
        return True


class FakeSubscriptions:
    async def missing_channels(self, bot, user_id, channels):
        return []


class FakeQueue:
    def __init__(self) -> None:
        self.submissions = []

    async def submit(self, user_id, job, *, on_cancel):
        self.submissions.append((user_id, job, on_cancel))
        return 1


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        admin_ids=frozenset(),
        download_request_ttl_minutes=30,
        max_concurrent_downloads=3,
    )


@pytest.mark.asyncio
async def test_youtube_link_creates_request_and_quality_callback(session_and_user) -> None:
    session, user = session_and_user
    message = FakeIncomingMessage("https://youtu.be/dQw4w9WgXcQ")

    await receive_url(
        message=message,  # type: ignore[arg-type]
        bot=object(),  # type: ignore[arg-type]
        session=session,
        db_user=user,
        settings=settings(),  # type: ignore[arg-type]
        media_registry=MediaRegistry.default(),
        metadata_service=FakeMetadataService(),  # type: ignore[arg-type]
        rate_limiter=FakeRateLimiter(),  # type: ignore[arg-type]
        subscription_service=FakeSubscriptions(),  # type: ignore[arg-type]
        download_queue=FakeQueue(),  # type: ignore[arg-type]
        download_manager=object(),  # type: ignore[arg-type]
    )

    request = await session.scalar(select(DownloadRequest))
    assert request is not None
    assert request.source is MediaSource.YOUTUBE
    button = message.status.reply_markup.inline_keyboard[0][0]
    assert button.callback_data == f"media:{request.id}:video_720"


@pytest.mark.asyncio
async def test_tiktok_link_is_automatically_submitted_to_queue(session_and_user) -> None:
    session, user = session_and_user
    message = FakeIncomingMessage("https://www.tiktok.com/@creator/video/7461234567890123456")
    queue = FakeQueue()
    metadata_service = FakeMetadataService()

    await receive_url(
        message=message,  # type: ignore[arg-type]
        bot=object(),  # type: ignore[arg-type]
        session=session,
        db_user=user,
        settings=settings(),  # type: ignore[arg-type]
        media_registry=MediaRegistry.default(),
        metadata_service=metadata_service,  # type: ignore[arg-type]
        rate_limiter=FakeRateLimiter(),  # type: ignore[arg-type]
        subscription_service=FakeSubscriptions(),  # type: ignore[arg-type]
        download_queue=queue,  # type: ignore[arg-type]
        download_manager=object(),  # type: ignore[arg-type]
    )

    assert len(queue.submissions) == 1
    download = await session.scalar(select(Download))
    assert download is not None
    assert download.source is MediaSource.TIKTOK
    assert download.requested_option == "best_video"
    assert download.requested_quality is None
    assert await session.scalar(select(DownloadRequest)) is None
    assert metadata_service.calls == []


@pytest.mark.asyncio
async def test_tiktok_short_link_gets_stable_internal_media_id(session_and_user) -> None:
    session, user = session_and_user
    message = FakeIncomingMessage("https://vm.tiktok.com/ZM1234abc/")

    await receive_url(
        message=message,  # type: ignore[arg-type]
        bot=object(),  # type: ignore[arg-type]
        session=session,
        db_user=user,
        settings=settings(),  # type: ignore[arg-type]
        media_registry=MediaRegistry.default(),
        metadata_service=FakeMetadataService(),  # type: ignore[arg-type]
        rate_limiter=FakeRateLimiter(),  # type: ignore[arg-type]
        subscription_service=FakeSubscriptions(),  # type: ignore[arg-type]
        download_queue=FakeQueue(),  # type: ignore[arg-type]
        download_manager=object(),  # type: ignore[arg-type]
    )

    download = await session.scalar(select(Download))
    assert download is not None
    assert download.source_media_id.startswith("url_")
    assert len(download.source_media_id) == 36
