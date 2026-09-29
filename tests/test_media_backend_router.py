from pathlib import Path

import pytest

from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    DownloadBackend,
    MediaDownloadRequest,
)
from app.services.media.backends.router import MediaBackendRouter
from app.services.media.models import (
    DownloadPolicy,
    MediaArtifact,
    MediaKind,
    MediaPackage,
    MediaSource,
)


class FakeBackend:
    def __init__(
        self,
        name: DownloadBackend,
        *,
        result: MediaPackage | None = None,
        error_kind: BackendErrorKind | None = None,
        create_partial: bool = False,
    ) -> None:
        self.name = name
        self.result = result
        self.error_kind = error_kind
        self.create_partial = create_partial
        self.calls = 0
        self.partial_seen_on_entry: bool | None = None

    async def download(self, request, job_dir, progress):
        self.calls += 1
        self.partial_seen_on_entry = (job_dir / "media.part").exists()
        if self.create_partial:
            (job_dir / "media.part").write_bytes(b"partial")
        if self.error_kind is not None:
            raise BackendError("backend failed", kind=self.error_kind)
        assert self.result is not None
        return self.result


def media_request(source: MediaSource) -> MediaDownloadRequest:
    return MediaDownloadRequest(
        source=source,
        url="https://example.invalid/video",
        option_key="best_video",
        policy=DownloadPolicy(format_selector="best"),
    )


def media_package(tmp_path: Path) -> MediaPackage:
    path = tmp_path / "media.mp4"
    path.write_bytes(b"video")
    return MediaPackage(
        artifacts=(MediaArtifact(path=path, media_kind=MediaKind.VIDEO),)
    )


@pytest.mark.asyncio
async def test_tiktok_retries_with_ytdlp_after_retryable_cobalt_failure(
    tmp_path: Path,
) -> None:
    cobalt = FakeBackend(
        DownloadBackend.COBALT,
        error_kind=BackendErrorKind.RETRYABLE,
        create_partial=True,
    )
    ytdlp = FakeBackend(DownloadBackend.YTDLP, result=media_package(tmp_path))
    fallbacks: list[tuple[DownloadBackend, DownloadBackend]] = []

    async def progress(stage: str, percent: float | None) -> None:
        return None

    async def on_fallback(current: DownloadBackend, following: DownloadBackend) -> None:
        fallbacks.append((current, following))

    router = MediaBackendRouter(
        {MediaSource.TIKTOK: (cobalt, ytdlp), MediaSource.YOUTUBE: (ytdlp,)}
    )
    result = await router.download(
        media_request(MediaSource.TIKTOK),
        tmp_path,
        progress,
        on_fallback,
    )

    assert result.backend is DownloadBackend.YTDLP
    assert cobalt.calls == 1
    assert ytdlp.calls == 1
    assert ytdlp.partial_seen_on_entry is False
    assert fallbacks == [(DownloadBackend.COBALT, DownloadBackend.YTDLP)]


@pytest.mark.asyncio
async def test_youtube_does_not_call_cobalt(tmp_path: Path) -> None:
    cobalt = FakeBackend(DownloadBackend.COBALT, result=media_package(tmp_path))
    ytdlp = FakeBackend(DownloadBackend.YTDLP, result=media_package(tmp_path))
    router = MediaBackendRouter(
        {MediaSource.TIKTOK: (cobalt, ytdlp), MediaSource.YOUTUBE: (ytdlp,)}
    )

    async def progress(stage: str, percent: float | None) -> None:
        return None

    async def on_fallback(current: DownloadBackend, following: DownloadBackend) -> None:
        return None

    result = await router.download(
        media_request(MediaSource.YOUTUBE), tmp_path, progress, on_fallback
    )

    assert result.backend is DownloadBackend.YTDLP
    assert cobalt.calls == 0
    assert ytdlp.calls == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [
        BackendErrorKind.TOO_LARGE,
        BackendErrorKind.CANCELLED,
        BackendErrorKind.FILESYSTEM,
    ],
)
async def test_non_retryable_backend_error_does_not_fallback(
    tmp_path: Path,
    kind: BackendErrorKind,
) -> None:
    cobalt = FakeBackend(DownloadBackend.COBALT, error_kind=kind)
    ytdlp = FakeBackend(DownloadBackend.YTDLP, result=media_package(tmp_path))
    router = MediaBackendRouter({MediaSource.TIKTOK: (cobalt, ytdlp)})

    async def progress(stage: str, percent: float | None) -> None:
        return None

    async def on_fallback(current: DownloadBackend, following: DownloadBackend) -> None:
        raise AssertionError("fallback must not be called")

    with pytest.raises(BackendError) as raised:
        await router.download(
            media_request(MediaSource.TIKTOK), tmp_path, progress, on_fallback
        )

    assert raised.value.kind is kind
    assert ytdlp.calls == 0
