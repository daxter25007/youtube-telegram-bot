from __future__ import annotations

import os

import aiohttp
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.youtube.metadata import MetadataService

pytestmark = pytest.mark.integration


@pytest.mark.asyncio
async def test_postgres_connection() -> None:
    url = os.environ["DATABASE_URL"]
    engine = create_async_engine(url)
    try:
        async with engine.connect() as connection:
            assert await connection.scalar(text("SELECT 1")) == 1
    finally:
        await engine.dispose()


@pytest.mark.asyncio
async def test_local_bot_api_get_me() -> None:
    base = os.environ["TELEGRAM_LOCAL_API_URL"].rstrip("/")
    token = os.environ["BOT_TOKEN"]
    async with (
        aiohttp.ClientSession() as session,
        session.post(f"{base}/bot{token}/getMe") as response,
    ):
        payload = await response.json()
    assert response.status == 200
    assert payload["ok"] is True


@pytest.mark.asyncio
async def test_youtube_metadata_when_url_is_provided() -> None:
    url = os.getenv("INTEGRATION_YOUTUBE_URL")
    if not url:
        pytest.skip("INTEGRATION_YOUTUBE_URL is not set")
    metadata = await MetadataService(
        timeout_seconds=120,
        max_upload_bytes=1950 * 1024 * 1024,
    ).fetch(url)
    assert metadata.video_id
    assert metadata.qualities
