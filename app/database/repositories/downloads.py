from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Download, DownloadStatus
from app.services.media.models import MediaKind, MediaSource


class DownloadRepository:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create(
        self,
        *,
        user_id: int,
        source: MediaSource,
        media_kind: MediaKind,
        source_media_id: str,
        source_url: str,
        video_title: str,
        requested_option: str,
        requested_quality: int | None,
        status: DownloadStatus = DownloadStatus.CREATED,
    ) -> Download:
        item = Download(
            user_id=user_id,
            source=source,
            media_kind=media_kind,
            source_media_id=source_media_id,
            source_url=source_url,
            video_title=video_title,
            requested_option=requested_option,
            requested_quality=requested_quality,
            status=status,
        )
        self.session.add(item)
        await self.session.flush()
        return item

    async def get(self, download_id: int) -> Download | None:
        return await self.session.get(Download, download_id)

    async def set_status(
        self,
        download_id: int,
        status: DownloadStatus,
        *,
        error_type: str | None = None,
        actual_quality: int | None = None,
        file_size: int | None = None,
        processing_seconds: float | None = None,
    ) -> None:
        item = await self.session.get(Download, download_id)
        if item is None:
            return
        item.status = status
        item.error_type = error_type
        if status == DownloadStatus.DOWNLOADING and item.started_at is None:
            item.started_at = datetime.now(UTC)
        if status in {
            DownloadStatus.COMPLETED,
            DownloadStatus.FAILED,
            DownloadStatus.TOO_LARGE,
            DownloadStatus.TIMEOUT,
            DownloadStatus.CANCELLED,
        }:
            item.completed_at = datetime.now(UTC)
        if actual_quality is not None:
            item.actual_quality = actual_quality
        if file_size is not None:
            item.file_size = file_size
        if processing_seconds is not None:
            item.processing_seconds = processing_seconds
        await self.session.flush()

    async def recent(self, limit: int = 20) -> list[Download]:
        result = await self.session.scalars(
            select(Download).order_by(Download.created_at.desc()).limit(limit)
        )
        return list(result)

    async def cancel_incomplete_after_restart(self) -> int:
        incomplete = [
            DownloadStatus.QUEUED,
            DownloadStatus.DOWNLOADING,
            DownloadStatus.MERGING,
            DownloadStatus.UPLOADING,
        ]
        result = await self.session.execute(
            update(Download)
            .where(Download.status.in_(incomplete))
            .values(
                status=DownloadStatus.CANCELLED,
                error_type="restart",
                completed_at=datetime.now(UTC),
            )
        )
        return result.rowcount or 0  # type: ignore[attr-defined]
