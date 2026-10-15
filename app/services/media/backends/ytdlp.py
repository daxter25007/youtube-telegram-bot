from __future__ import annotations

from pathlib import Path

from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    DownloadBackend,
    MediaDownloadRequest,
    MediaPartFailure,
    PreparedMedia,
)
from app.services.media.downloader import DownloadError, MediaDownloader, ProgressCallback
from app.services.media.models import (
    DownloadPolicy,
    MediaKind,
    MediaPackage,
    MediaPart,
    MediaSource,
)

BUNDLE_OPTIONS = frozenset({"tiktok_bundle", "instagram_bundle", "pinterest_bundle"})


class YtDlpBackend:
    name = DownloadBackend.YTDLP

    def __init__(self, downloader: MediaDownloader) -> None:
        self.downloader = downloader

    async def prepare(self, request: MediaDownloadRequest) -> PreparedMedia:
        if request.source is MediaSource.PINTEREST and request.option_key == "pinterest_bundle":
            try:
                if await self.downloader.is_pinterest_photo(request.url):
                    return PreparedMedia(
                        request=request,
                        parts=(
                            MediaPart(
                                key="photo_000",
                                media_kind=MediaKind.PHOTO,
                                position=0,
                            ),
                        ),
                    )
            except DownloadError as exc:
                raise BackendError(str(exc), kind=BackendErrorKind.RETRYABLE) from exc
        if request.option_key in BUNDLE_OPTIONS:
            parts = (
                MediaPart(key="video", media_kind=MediaKind.VIDEO, position=0),
                MediaPart(key="audio", media_kind=MediaKind.AUDIO, position=1),
            )
        else:
            parts = (
                MediaPart(
                    key=request.option_key,
                    media_kind=request.policy.media_kind,
                    position=0,
                ),
            )
        return PreparedMedia(request=request, parts=parts)

    async def download_prepared(
        self,
        prepared: PreparedMedia,
        include_keys: frozenset[str],
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage:
        artifacts = []
        failures: list[MediaPartFailure] = []
        for part in prepared.parts:
            if part.key not in include_keys:
                continue
            policy = prepared.request.policy
            if prepared.request.option_key in BUNDLE_OPTIONS:
                if part.media_kind is MediaKind.VIDEO:
                    format_selector = (
                        "bestvideo[ext=mp4]/bestvideo/best[ext=mp4]/best"
                        if prepared.request.source is MediaSource.PINTEREST
                        else "best[ext=mp4]/best"
                    )
                    policy = DownloadPolicy(format_selector=format_selector)
                else:
                    policy = DownloadPolicy(
                        format_selector="bestaudio/best",
                        media_kind=MediaKind.AUDIO,
                        extract_audio=True,
                        audio_format="best",
                    )
            try:
                if (
                    prepared.request.source is MediaSource.PINTEREST
                    and part.media_kind is MediaKind.PHOTO
                ):
                    await progress("downloading", None)
                    package = await self.downloader.download_thumbnail(
                        url=prepared.request.url,
                        job_dir=job_dir,
                        part_key=part.key,
                    )
                else:
                    package = await self.downloader.download(
                        url=prepared.request.url,
                        policy=policy,
                        job_dir=job_dir,
                        progress=progress,
                        part_key=part.key,
                    )
            except DownloadError:
                failures.append(
                    MediaPartFailure(
                        part_key=part.key,
                        media_kind=part.media_kind,
                        error_kind=BackendErrorKind.RETRYABLE,
                    )
                )
                continue
            artifacts.extend(package.artifacts)
        return MediaPackage(artifacts=tuple(artifacts), failures=tuple(failures))
