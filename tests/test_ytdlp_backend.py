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

    async def is_pinterest_photo(self, url: str) -> bool:
        return False

    async def download(self, *, policy, part_key, **kwargs):
        if part_key == "audio":
            raise DownloadError("audio unavailable")
        path = self.root / "video.mp4"
        path.write_bytes(b"video")
        return MediaPackage(artifacts=(MediaArtifact(path, MediaKind.VIDEO, part_key="video"),))


class PinterestVideoOnlyDownloader:
    def __init__(self, root: Path) -> None:
        self.root = root

    async def is_pinterest_photo(self, url: str) -> bool:
        return False

    async def download(self, *, policy, part_key, **kwargs):
        if not policy.format_selector.startswith("bestvideo"):
            raise DownloadError("Requested format is not available")
        path = self.root / "video.mp4"
        path.write_bytes(b"video")
        return MediaPackage(artifacts=(MediaArtifact(path, MediaKind.VIDEO, part_key=part_key),))


class PinterestPhotoDownloader:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.download_calls = 0
        self.thumbnail_calls = 0

    async def is_pinterest_photo(self, url: str) -> bool:
        return True

    async def download(self, **kwargs):
        self.download_calls += 1
        raise AssertionError("Для фото не должен запускаться обычный video/audio download")

    async def download_thumbnail(self, *, part_key: str, **kwargs):
        self.thumbnail_calls += 1
        path = self.root / f"{part_key}.jpg"
        path.write_bytes(b"photo")
        return MediaPackage(artifacts=(MediaArtifact(path, MediaKind.PHOTO, part_key=part_key),))


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


@pytest.mark.asyncio
async def test_pinterest_video_fallback_keeps_video_when_audio_is_unavailable(
    tmp_path: Path,
) -> None:
    backend = YtDlpBackend(PartialDownloader(tmp_path))  # type: ignore[arg-type]
    request = MediaDownloadRequest(
        source=MediaSource.PINTEREST,
        url="https://www.pinterest.com/pin/123456789/",
        option_key="pinterest_bundle",
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
async def test_pinterest_fallback_selects_video_only_stream(tmp_path: Path) -> None:
    backend = YtDlpBackend(PinterestVideoOnlyDownloader(tmp_path))  # type: ignore[arg-type]
    request = MediaDownloadRequest(
        source=MediaSource.PINTEREST,
        url="https://www.pinterest.com/pin/377106168820372449/",
        option_key="pinterest_bundle",
        policy=DownloadPolicy(format_selector="best"),
    )
    prepared = await backend.prepare(request)

    async def progress(stage: str, percent: float | None) -> None:
        return None

    package = await backend.download_prepared(
        prepared,
        frozenset({"video"}),
        tmp_path,
        progress,
    )

    assert [artifact.part_key for artifact in package.artifacts] == ["video"]
    assert package.failures == ()


@pytest.mark.asyncio
async def test_pinterest_photo_fallback_prepares_and_downloads_photo(tmp_path: Path) -> None:
    downloader = PinterestPhotoDownloader(tmp_path)
    backend = YtDlpBackend(downloader)  # type: ignore[arg-type]
    request = MediaDownloadRequest(
        source=MediaSource.PINTEREST,
        url="https://www.pinterest.com/pin/675328906665725422/",
        option_key="pinterest_bundle",
        policy=DownloadPolicy(format_selector="best"),
    )
    prepared = await backend.prepare(request)

    async def progress(stage: str, percent: float | None) -> None:
        return None

    package = await backend.download_prepared(
        prepared,
        frozenset({"photo_000"}),
        tmp_path,
        progress,
    )

    assert [(part.key, part.media_kind) for part in prepared.parts] == [
        ("photo_000", MediaKind.PHOTO)
    ]
    assert [(artifact.part_key, artifact.media_kind) for artifact in package.artifacts] == [
        ("photo_000", MediaKind.PHOTO)
    ]
    assert package.failures == ()
    assert downloader.download_calls == 0
    assert downloader.thumbnail_calls == 1
