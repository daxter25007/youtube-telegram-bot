from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine

from app.database.base import Base
from app.database.models import DownloadStatus, User
from app.database.repositories.channels import ChannelRepository
from app.database.repositories.downloads import DownloadRepository
from app.database.repositories.requests import DownloadRequestRepository


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
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        youtube_video_id="dQw4w9WgXcQ",
        metadata={"title": "test", "qualities": []},
    )
    await session.commit()
    assert await request_repo.get_owned_valid(request.id, 100, 30) is not None
    assert await request_repo.get_owned_valid(request.id, 999, 30) is None
    future = datetime.now(UTC) + timedelta(minutes=31)
    assert await request_repo.get_owned_valid(request.id, 100, 30, now=future) is None

    channel = await ChannelRepository(session).add(
        chat_id=-100123,
        username="@channel",
        title="Channel",
        invite_url="https://t.me/channel",
    )
    assert (await ChannelRepository(session).active())[0].id == channel.id
    await ChannelRepository(session).toggle(channel.id)
    assert await ChannelRepository(session).active() == []

    download = await DownloadRepository(session).create(
        user_id=user.id,
        youtube_video_id="dQw4w9WgXcQ",
        youtube_url="https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        video_title="test",
        requested_quality=720,
        status=DownloadStatus.QUEUED,
    )
    assert await DownloadRepository(session).get(download.id) is download
    assert await DownloadRepository(session).cancel_incomplete_after_restart() == 1
    assert download.status == DownloadStatus.CANCELLED
