from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage
from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.bot.keyboards.quality import quality_keyboard
from app.bot.routers.downloader import _enqueue_option, receive_url
from app.database.base import Base
from app.database.models import Download, DownloadRequest, DownloadStatus, User
from app.database.repositories.media_cache import MediaCacheRepository
from app.database.repositories.requests import DownloadRequestRepository
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


class FakeCachedDownloadManager:
    def __init__(self) -> None:
        self.calls = 0

    async def try_cached(self, **kwargs):
        self.calls += 1
        return True


class FakeCacheMissDownloadManager:
    async def try_cached(self, **kwargs):
        return False


class FakeUnavailableCachedDownloadManager:
    async def try_cached(self, **kwargs):
        raise TelegramNetworkError(
            method=SendMessage(chat_id=123, text="cached"),
            message="network error",
        )


def settings() -> SimpleNamespace:
    return SimpleNamespace(
        admin_ids=frozenset(),
        download_request_ttl_minutes=30,
        max_concurrent_downloads=3,
    )


def test_quality_keyboard_marks_cached_and_waiting_options() -> None:
    options = [
        DownloadOption(
            key="video_720",
            label="720p",
            media_kind=MediaKind.VIDEO,
            height=720,
        ),
        DownloadOption(
            key="video_1080",
            label="1080p",
            media_kind=MediaKind.VIDEO,
            height=1080,
        ),
    ]

    keyboard = quality_keyboard(
        "request-id",
        options,
        cached_option_keys={"video_720"},
    )

    assert [row[0].text for row in keyboard.inline_keyboard] == ["⚡ 720p", "⏳ 1080p"]


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
        download_manager=FakeCacheMissDownloadManager(),  # type: ignore[arg-type]
    )

    request = await session.scalar(select(DownloadRequest))
    assert request is not None
    assert request.source is MediaSource.YOUTUBE
    button = message.status.reply_markup.inline_keyboard[0][0]
    assert button.callback_data == f"media:{request.id}:video_720"


@pytest.mark.asyncio
async def test_youtube_quality_button_marks_cached_option(session_and_user) -> None:
    session, user = session_and_user
    await MediaCacheRepository(session).store(
        source=MediaSource.YOUTUBE,
        source_media_id="dQw4w9WgXcQ",
        media_kind=MediaKind.VIDEO,
        requested_option="video_720",
        telegram_file_id="cached-file-id",
        telegram_file_unique_id="cached-unique-id",
        sent_as_document=False,
        actual_quality=720,
        file_size=1234,
    )
    await session.commit()
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
        download_manager=FakeCacheMissDownloadManager(),  # type: ignore[arg-type]
    )

    button = message.status.reply_markup.inline_keyboard[0][0]
    assert button.text == "⚡ 720p"


@pytest.mark.asyncio
async def test_cached_selection_bypasses_download_queue(session_and_user) -> None:
    session, user = session_and_user
    option = DownloadOption(
        key="video_720",
        label="720p",
        media_kind=MediaKind.VIDEO,
        height=720,
    )
    request = await DownloadRequestRepository(session).create(
        user_id=user.id,
        telegram_user_id=123,
        source=MediaSource.YOUTUBE,
        media_kind=MediaKind.VIDEO,
        source_url="https://youtu.be/dQw4w9WgXcQ",
        source_media_id="dQw4w9WgXcQ",
        metadata={"title": "YouTube example", "options": [option.as_dict()]},
    )
    await session.commit()
    queue = FakeQueue()
    manager = FakeCachedDownloadManager()
    progress = FakeStatusMessage()

    await _enqueue_option(
        progress_message=progress,  # type: ignore[arg-type]
        telegram_user_id=123,
        request=request,
        option=option,
        session=session,
        settings=settings(),  # type: ignore[arg-type]
        media_registry=MediaRegistry.default(),
        download_queue=queue,  # type: ignore[arg-type]
        download_manager=manager,  # type: ignore[arg-type]
        bot=object(),  # type: ignore[arg-type]
    )

    download = await session.scalar(select(Download))
    assert manager.calls == 1
    assert queue.submissions == []
    assert download is not None
    assert download.status is DownloadStatus.CREATED
    assert progress.texts == []


@pytest.mark.asyncio
async def test_cached_send_network_error_is_recorded_without_queueing(session_and_user) -> None:
    session, user = session_and_user
    option = DownloadOption(
        key="video_720",
        label="720p",
        media_kind=MediaKind.VIDEO,
        height=720,
    )
    request = await DownloadRequestRepository(session).create(
        user_id=user.id,
        telegram_user_id=123,
        source=MediaSource.YOUTUBE,
        media_kind=MediaKind.VIDEO,
        source_url="https://youtu.be/dQw4w9WgXcQ",
        source_media_id="dQw4w9WgXcQ",
        metadata={"title": "YouTube example", "options": [option.as_dict()]},
    )
    await session.commit()
    queue = FakeQueue()
    progress = FakeStatusMessage()

    await _enqueue_option(
        progress_message=progress,  # type: ignore[arg-type]
        telegram_user_id=123,
        request=request,
        option=option,
        session=session,
        settings=settings(),  # type: ignore[arg-type]
        media_registry=MediaRegistry.default(),
        download_queue=queue,  # type: ignore[arg-type]
        download_manager=FakeUnavailableCachedDownloadManager(),  # type: ignore[arg-type]
        bot=object(),  # type: ignore[arg-type]
    )

    download = await session.scalar(select(Download))
    assert queue.submissions == []
    assert download is not None
    assert download.status is DownloadStatus.FAILED
    assert download.error_type == "telegram_api"
    assert progress.texts[-1] == "❌ Не удалось отправить файл в Telegram."


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
        download_manager=FakeCacheMissDownloadManager(),  # type: ignore[arg-type]
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
        download_manager=FakeCacheMissDownloadManager(),  # type: ignore[arg-type]
    )

    download = await session.scalar(select(Download))
    assert download is not None
    assert download.source_media_id.startswith("url_")
    assert len(download.source_media_id) == 36
