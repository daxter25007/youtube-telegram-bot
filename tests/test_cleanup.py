from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.exceptions import TelegramNetworkError
from aiogram.methods import SendMessage

from app.database.base import Base
from app.database.models import DownloadStatus, User
from app.database.repositories.downloads import DownloadRepository
from app.database.session import Database
from app.services.cleanup import TempStorage
from app.services.download_manager import DownloadManager
from app.services.youtube.downloader import DownloadedMedia, DownloadError


class FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def edit_text(self, text: str) -> None:
        self.texts.append(text)


class FakeSubscriptions:
    async def missing_channels(self, bot, user_id, channels):
        return []


class FakeTelegramFiles:
    async def send(self, bot, **kwargs):
        return None


class FailingTelegramFiles:
    async def send(self, bot, **kwargs):
        raise TelegramNetworkError(
            method=SendMessage(chat_id=1, text="upload"),
            message="network error",
        )


class SuccessfulDownloader:
    async def download(self, *, job_dir: Path, progress, **kwargs):
        path = job_dir / "media.mp4"
        path.write_bytes(b"video")
        await progress("downloading", 50)
        return DownloadedMedia(path=path, width=10, height=360, duration=1)


class FailingDownloader:
    async def download(self, **kwargs):
        raise DownloadError("failure")


class TimeoutDownloader:
    async def download(self, **kwargs):
        await asyncio.sleep(1)
        raise AssertionError


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
            youtube_video_id="dQw4w9WgXcQ",
            youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
            video_title="test",
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
        downloader=downloader,
        subscriptions=FakeSubscriptions(),  # type: ignore[arg-type]
        telegram_files=telegram_files or FakeTelegramFiles(),  # type: ignore[arg-type]
    )
    message = FakeMessage()
    await manager.run(
        bot=object(),  # type: ignore[arg-type]
        chat_id=123,
        telegram_user_id=123,
        user_id=user_id,
        download_id=download_id,
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        title="test",
        quality=360,
        progress_message=message,  # type: ignore[arg-type]
    )
    async with database.session() as session:
        status = (await DownloadRepository(session).get(download_id)).status
    jobs = list((tmp_path / "jobs").iterdir())
    await database.close()
    return status, jobs, message.texts


@pytest.mark.asyncio
async def test_successful_cleanup(tmp_path: Path) -> None:
    status, jobs, _ = await run_case(tmp_path, SuccessfulDownloader())
    assert status == DownloadStatus.COMPLETED
    assert jobs == []


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
