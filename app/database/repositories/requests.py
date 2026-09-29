from __future__ import annotations

import secrets
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import DownloadRequest
from app.services.media.models import MediaKind, MediaSource


class DownloadRequestRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        telegram_user_id: int,
        source: MediaSource,
        media_kind: MediaKind,
        source_url: str,
        source_media_id: str,
        metadata: dict[str, object],
    ) -> DownloadRequest:
        request = DownloadRequest(
            id=secrets.token_urlsafe(8)[:12],
            user_id=user_id,
            telegram_user_id=telegram_user_id,
            source=source,
            media_kind=media_kind,
            source_url=source_url,
            source_media_id=source_media_id,
            metadata_json=metadata,
        )
        self.session.add(request)
        await self.session.flush()
        return request

    async def get_owned_valid(
        self,
        request_id: str,
        telegram_user_id: int,
        ttl_minutes: int,
        *,
        now: datetime | None = None,
    ) -> DownloadRequest | None:
        cutoff = (now or datetime.now(UTC)) - timedelta(minutes=ttl_minutes)
        return await self.session.scalar(
            select(DownloadRequest).where(
                DownloadRequest.id == request_id,
                DownloadRequest.telegram_user_id == telegram_user_id,
                DownloadRequest.created_at >= cutoff,
            )
        )

    async def delete(self, request_id: str) -> None:
        await self.session.execute(delete(DownloadRequest).where(DownloadRequest.id == request_id))

    async def cleanup_expired(self, ttl_minutes: int) -> int:
        cutoff = datetime.now(UTC) - timedelta(minutes=ttl_minutes)
        result = await self.session.execute(
            delete(DownloadRequest).where(DownloadRequest.created_at < cutoff)
        )
        return result.rowcount or 0  # type: ignore[attr-defined]
