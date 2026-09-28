from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.database.models import Download, DownloadStatus, User


class StatisticsService:
    async def summary(self, session: AsyncSession) -> dict[str, Any]:
        now = datetime.now(UTC)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        users = await session.execute(
            select(
                func.count(User.id),
                func.count(case((User.created_at >= today, 1))),
                func.count(case((User.created_at >= now - timedelta(days=7), 1))),
                func.count(case((User.created_at >= now - timedelta(days=30), 1))),
            )
        )
        downloads = await session.execute(
            select(
                func.count(Download.id),
                func.count(case((Download.status == DownloadStatus.COMPLETED, 1))),
                func.count(
                    case(
                        (
                            Download.status.in_(
                                [
                                    DownloadStatus.FAILED,
                                    DownloadStatus.TIMEOUT,
                                    DownloadStatus.TOO_LARGE,
                                    DownloadStatus.CANCELLED,
                                ]
                            ),
                            1,
                        )
                    )
                ),
                func.count(case((Download.created_at >= today, 1))),
                func.count(case((Download.created_at >= now - timedelta(days=7), 1))),
                func.count(case((Download.created_at >= now - timedelta(days=30), 1))),
                func.coalesce(
                    func.sum(
                        case(
                            (Download.status == DownloadStatus.COMPLETED, Download.file_size),
                            else_=0,
                        )
                    ),
                    0,
                ),
                func.coalesce(
                    func.avg(
                        case((Download.status == DownloadStatus.COMPLETED, Download.file_size))
                    ),
                    0,
                ),
                func.coalesce(
                    func.avg(
                        case(
                            (
                                Download.status == DownloadStatus.COMPLETED,
                                Download.processing_seconds,
                            )
                        )
                    ),
                    0,
                ),
            )
        )
        qualities = await session.execute(
            select(Download.requested_quality, func.count(Download.id).label("count"))
            .group_by(Download.requested_quality)
            .order_by(func.count(Download.id).desc())
            .limit(10)
        )
        top_users = await session.execute(
            select(User.telegram_id, User.username, User.downloads_count)
            .order_by(User.downloads_count.desc())
            .limit(10)
        )
        user_row = users.one()
        download_row = downloads.one()
        return {
            "users": tuple(user_row),
            "downloads": tuple(download_row),
            "qualities": list(qualities.all()),
            "top_users": list(top_users.all()),
        }
