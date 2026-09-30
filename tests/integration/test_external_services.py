from __future__ import annotations

import os

import aiohttp
import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.services.cobalt.client import CobaltClient
from app.services.cobalt.security import SafeRemoteUrlValidator
from app.services.media.metadata import YtDlpMetadataService
from app.services.media.registry import MediaRegistry

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
    registry = MediaRegistry.default()
    parsed = registry.parse(url)
    assert parsed is not None
    metadata = await YtDlpMetadataService(
        registry=registry,
        timeout_seconds=120,
        max_upload_bytes=1950 * 1024 * 1024,
    ).fetch(parsed)
    assert metadata.media_id
    assert metadata.options


@pytest.mark.asyncio
async def test_tiktok_metadata_when_url_is_provided() -> None:
    url = os.getenv("INTEGRATION_TIKTOK_URL")
    if not url:
        pytest.skip("INTEGRATION_TIKTOK_URL is not set")
    registry = MediaRegistry.default()
    parsed = registry.parse(url)
    assert parsed is not None
    metadata = await YtDlpMetadataService(
        registry=registry,
        timeout_seconds=120,
        max_upload_bytes=1950 * 1024 * 1024,
    ).fetch(parsed)
    assert metadata.media_id
    assert metadata.options[0].key == "tiktok_bundle"


@pytest.mark.asyncio
async def test_cobalt_prepares_tiktok_video_when_url_is_provided() -> None:
    url = os.getenv("INTEGRATION_TIKTOK_URL")
    base_url = os.getenv("COBALT_API_URL")
    if not url or not base_url:
        pytest.skip("INTEGRATION_TIKTOK_URL or COBALT_API_URL is not set")
    validator = SafeRemoteUrlValidator()
    async with aiohttp.ClientSession() as session:
        response = await CobaltClient(
            base_url=base_url,
            request_timeout_seconds=30,
            session=session,
            validator=validator,
        ).prepare(url)

    assert response.items
