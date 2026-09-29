from __future__ import annotations

from pathlib import Path

from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    DownloadBackend,
    MediaDownloadRequest,
)
from app.services.media.downloader import DownloadError, MediaDownloader, ProgressCallback
from app.services.media.models import MediaPackage


class YtDlpBackend:
    name = DownloadBackend.YTDLP

    def __init__(self, downloader: MediaDownloader) -> None:
        self.downloader = downloader

    async def download(
        self,
        request: MediaDownloadRequest,
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage:
        try:
            return await self.downloader.download(
                url=request.url,
                policy=request.policy,
                job_dir=job_dir,
                progress=progress,
            )
        except DownloadError as exc:
            raise BackendError(
                "yt-dlp backend не получил видео",
                kind=BackendErrorKind.RETRYABLE,
            ) from exc
