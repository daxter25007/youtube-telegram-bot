from pathlib import Path

import pytest

from app.services.media.backends.base import BackendErrorKind, MediaDownloadRequest
from app.services.media.backends.ytdlp import YtDlpBackend
from app.services.media.downloader import DownloadError
from app.services.media.models import (
    DownloadPolicy,
    MediaArtifact,
    MediaKind,
    MediaPackage,
    MediaSource,
)


class PartialDownloader:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def download(self, *, policy, part_key, **kwargs):
        if part_key == "audio":
            raise DownloadError("audio unavailable")
        path = self.root / "video.mp4"
        path.write_bytes(b"video")
        return MediaPackage(artifacts=(MediaArtifact(path, MediaKind.VIDEO, part_key="video"),))


@pytest.mark.asyncio
async def test_tiktok_audio_failure_keeps_downloaded_video(tmp_path: Path) -> None:
    backend = YtDlpBackend(PartialDownloader(tmp_path))  # type: ignore[arg-type]
    request = MediaDownloadRequest(
        source=MediaSource.TIKTOK,
        url="https://vm.tiktok.com/POST1/",
        option_key="tiktok_bundle",
        policy=DownloadPolicy(format_selector="best"),
    )
    prepared = await backend.prepare(request)

    async def progress(stage: str, percent: float | None) -> None:
        return None

    package = await backend.download_prepared(
        prepared, frozenset({"video", "audio"}), tmp_path, progress
    )

    assert [artifact.part_key for artifact in package.artifacts] == ["video"]
    assert [(failure.part_key, failure.error_kind) for failure in package.failures] == [
        ("audio", BackendErrorKind.RETRYABLE)
    ]


@pytest.mark.asyncio
async def test_instagram_audio_failure_keeps_downloaded_video(tmp_path: Path) -> None:
    backend = YtDlpBackend(PartialDownloader(tmp_path))  # type: ignore[arg-type]
    request = MediaDownloadRequest(
        source=MediaSource.INSTAGRAM,
        url="https://www.instagram.com/reel/DQw4w9WgXcQ/",
        option_key="instagram_bundle",
        policy=DownloadPolicy(format_selector="best"),
    )
    prepared = await backend.prepare(request)

    async def progress(stage: str, percent: float | None) -> None:
        return None

    package = await backend.download_prepared(
        prepared, frozenset({"video", "audio"}), tmp_path, progress
    )

    assert [artifact.part_key for artifact in package.artifacts] == ["video"]
    assert [(failure.part_key, failure.error_kind) for failure in package.failures] == [
        ("audio", BackendErrorKind.RETRYABLE)
    ]
