from pathlib import Path

import pytest

from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    DownloadBackend,
    MediaDownloadRequest,
    PreparedMedia,
)
from app.services.media.backends.router import MediaBackendRouter
from app.services.media.models import (
    DownloadPolicy,
    MediaArtifact,
    MediaKind,
    MediaPackage,
    MediaPart,
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
        self.prepare_calls = 0
        self.download_calls = 0
        self.include_keys: frozenset[str] = frozenset()
        self.partial_seen_on_entry: bool | None = None

    async def prepare(self, request):
        self.prepare_calls += 1
        return PreparedMedia(
            request=request,
            parts=(MediaPart(key="video", media_kind=MediaKind.VIDEO, position=0),),
        )

    async def download_prepared(self, prepared, include_keys, job_dir, progress):
        self.download_calls += 1
        self.include_keys = include_keys
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
        artifacts=(MediaArtifact(path=path, media_kind=MediaKind.VIDEO, part_key="video"),)
    )


async def all_parts(prepared: PreparedMedia) -> frozenset[str]:
    return frozenset(part.key for part in prepared.parts)


async def ignore_progress(stage: str, percent: float | None) -> None:
    return None


@pytest.mark.asyncio
async def test_router_prepares_then_downloads_only_callback_part_keys(tmp_path: Path) -> None:
    backend = FakeBackend(DownloadBackend.YTDLP, result=media_package(tmp_path))
    router = MediaBackendRouter({MediaSource.YOUTUBE: (backend,)})

    async def audio_only(prepared: PreparedMedia) -> frozenset[str]:
        return frozenset({"audio"})

    result = await router.download(
        media_request(MediaSource.YOUTUBE),
        tmp_path,
        ignore_progress,
        lambda current, following: None,  # type: ignore[arg-type]
        audio_only,
    )

    assert result.backend is DownloadBackend.YTDLP
    assert backend.prepare_calls == 1
    assert backend.download_calls == 1
    assert backend.include_keys == frozenset({"audio"})


@pytest.mark.asyncio
async def test_tiktok_retries_with_ytdlp_after_retryable_cobalt_failure(tmp_path: Path) -> None:
    cobalt = FakeBackend(
        DownloadBackend.COBALT,
        error_kind=BackendErrorKind.RETRYABLE,
        create_partial=True,
    )
    ytdlp = FakeBackend(DownloadBackend.YTDLP, result=media_package(tmp_path))
    fallbacks: list[tuple[DownloadBackend, DownloadBackend]] = []

    async def on_fallback(current: DownloadBackend, following: DownloadBackend) -> None:
        fallbacks.append((current, following))

    router = MediaBackendRouter({MediaSource.TIKTOK: (cobalt, ytdlp)})
    result = await router.download(
        media_request(MediaSource.TIKTOK),
        tmp_path,
        ignore_progress,
        on_fallback,
        all_parts,
    )

    assert result.backend is DownloadBackend.YTDLP
    assert cobalt.download_calls == 1
    assert ytdlp.download_calls == 1
    assert ytdlp.partial_seen_on_entry is False
    assert fallbacks == [(DownloadBackend.COBALT, DownloadBackend.YTDLP)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "kind",
    [BackendErrorKind.TOO_LARGE, BackendErrorKind.CANCELLED, BackendErrorKind.FILESYSTEM],
)
async def test_non_retryable_backend_error_does_not_fallback(
    tmp_path: Path,
    kind: BackendErrorKind,
) -> None:
    cobalt = FakeBackend(DownloadBackend.COBALT, error_kind=kind)
    ytdlp = FakeBackend(DownloadBackend.YTDLP, result=media_package(tmp_path))
    router = MediaBackendRouter({MediaSource.TIKTOK: (cobalt, ytdlp)})

    async def on_fallback(current: DownloadBackend, following: DownloadBackend) -> None:
        raise AssertionError("fallback must not be called")

    with pytest.raises(BackendError) as raised:
        await router.download(
            media_request(MediaSource.TIKTOK),
            tmp_path,
            ignore_progress,
            on_fallback,
            all_parts,
        )

    assert raised.value.kind is kind
    assert ytdlp.prepare_calls == 0
