import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.cobalt.models import CobaltMediaResponse, CobaltResponseKind
from app.services.cobalt.security import UnsafeRemoteUrlError
from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    MediaDownloadRequest,
)
from app.services.media.backends.cobalt import CobaltBackend
from app.services.media.models import DownloadPolicy, MediaKind, MediaSource


class FakeCobaltClient:
    base_url = "http://cobalt:9000"

    def __init__(self, response: CobaltMediaResponse) -> None:
        self.response = response

    async def prepare(self, url: str) -> CobaltMediaResponse:
        return self.response


class FakeContent:
    def __init__(self, chunks: list[bytes | BaseException]) -> None:
        self.chunks = chunks

    async def iter_chunked(self, size: int):
        for chunk in self.chunks:
            if isinstance(chunk, BaseException):
                raise chunk
            yield chunk


class FakeResponse:
    def __init__(
        self,
        chunks: list[bytes | BaseException],
        *,
        status: int = 200,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.status = status
        self.headers = headers or {}
        self.content = FakeContent(chunks)
        self.closed = False

    async def close(self) -> None:
        self.closed = True


class FakeOpener:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.urls: list[str] = []

    async def open(self, validated):
        self.urls.append(validated.url)
        return self.responses.pop(0)


class FailingOpener:
    async def open(self, validated):
        raise OSError("connection refused")


class FakeValidator:
    def __init__(self, rejected: set[str] | None = None) -> None:
        self.rejected = rejected or set()
        self.calls: list[tuple[str, str | None]] = []

    async def validate(self, url: str, *, allowed_internal_origin=None):
        self.calls.append((url, allowed_internal_origin))
        if url in self.rejected:
            raise UnsafeRemoteUrlError("unsafe")
        return SimpleNamespace(url=url, is_internal=allowed_internal_origin is not None)


def request() -> MediaDownloadRequest:
    return MediaDownloadRequest(
        source=MediaSource.TIKTOK,
        url="https://www.tiktok.com/@creator/video/7461234567890123456",
        option_key="best_video",
        policy=DownloadPolicy(format_selector="best"),
    )


def backend(response: FakeResponse, *, limit: int = 100) -> CobaltBackend:
    client = FakeCobaltClient(
        CobaltMediaResponse(
            kind=CobaltResponseKind.TUNNEL,
            url="http://cobalt:9000/tunnel?id=secret",
            filename="video.mp4",
        )
    )
    return CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(response),  # type: ignore[arg-type]
        max_upload_bytes=limit,
    )


@pytest.mark.asyncio
async def test_cobalt_backend_streams_to_final_file_and_reports_progress(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    response = FakeResponse([b"abc", b"def"], headers={"Content-Length": "6"})
    downloader = backend(response)

    async def fake_probe(path: Path):
        return SimpleNamespace(width=1080, height=1920, duration=15)

    monkeypatch.setattr("app.services.media.backends.cobalt.probe_media", fake_probe)
    progress: list[tuple[str, float | None]] = []

    async def record(stage: str, percent: float | None) -> None:
        progress.append((stage, percent))

    package = await downloader.download(request(), tmp_path, record)

    assert not (tmp_path / "media.part").exists()
    assert (tmp_path / "media.mp4").read_bytes() == b"abcdef"
    assert progress == [("downloading", 50.0), ("downloading", 100.0)]
    assert package.artifacts[0].media_kind is MediaKind.VIDEO
    assert package.artifacts[0].height == 1920
    assert response.closed is True


@pytest.mark.asyncio
async def test_cobalt_backend_rejects_oversized_content_length(tmp_path: Path) -> None:
    response = FakeResponse([], headers={"Content-Length": "101"})

    with pytest.raises(BackendError) as raised:
        await backend(response, limit=100).download(request(), tmp_path, _ignore_progress)

    assert raised.value.kind is BackendErrorKind.TOO_LARGE
    assert not (tmp_path / "media.part").exists()


@pytest.mark.asyncio
@pytest.mark.parametrize("headers", [{}, {"Content-Length": "1"}])
async def test_cobalt_backend_enforces_actual_stream_size(
    tmp_path: Path,
    headers: dict[str, str],
) -> None:
    response = FakeResponse([b"a" * 80, b"b" * 30], headers=headers)

    with pytest.raises(BackendError) as raised:
        await backend(response, limit=100).download(request(), tmp_path, _ignore_progress)

    assert raised.value.kind is BackendErrorKind.TOO_LARGE
    assert not (tmp_path / "media.part").exists()
    assert not (tmp_path / "media.mp4").exists()


@pytest.mark.asyncio
async def test_cobalt_backend_removes_partial_after_stream_error(tmp_path: Path) -> None:
    response = FakeResponse([b"partial", OSError("connection lost")])

    with pytest.raises(BackendError) as raised:
        await backend(response).download(request(), tmp_path, _ignore_progress)

    assert raised.value.kind is BackendErrorKind.RETRYABLE
    assert not (tmp_path / "media.part").exists()


@pytest.mark.asyncio
async def test_cobalt_backend_wraps_connection_error_for_fallback(tmp_path: Path) -> None:
    downloader = CobaltBackend(
        client=FakeCobaltClient(
            CobaltMediaResponse(
                kind=CobaltResponseKind.TUNNEL,
                url="http://cobalt:9000/tunnel?id=secret",
                filename="video.mp4",
            )
        ),  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FailingOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    with pytest.raises(BackendError) as raised:
        await downloader.download(request(), tmp_path, _ignore_progress)

    assert raised.value.kind is BackendErrorKind.RETRYABLE


@pytest.mark.asyncio
async def test_cobalt_backend_removes_partial_after_cancellation(tmp_path: Path) -> None:
    response = FakeResponse([b"partial", asyncio.CancelledError()])

    with pytest.raises(asyncio.CancelledError):
        await backend(response).download(request(), tmp_path, _ignore_progress)

    assert not (tmp_path / "media.part").exists()


@pytest.mark.asyncio
async def test_cobalt_backend_revalidates_and_rejects_unsafe_redirect(tmp_path: Path) -> None:
    source_url = "https://cdn.example/video.mp4"
    private_url = "http://127.0.0.1/private.mp4"
    validator = FakeValidator({private_url})
    opener = FakeOpener(FakeResponse([], status=302, headers={"Location": private_url}))
    downloader = CobaltBackend(
        client=FakeCobaltClient(
            CobaltMediaResponse(
                kind=CobaltResponseKind.REDIRECT,
                url=source_url,
                filename="video.mp4",
            )
        ),  # type: ignore[arg-type]
        validator=validator,  # type: ignore[arg-type]
        opener=opener,  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    with pytest.raises(BackendError) as raised:
        await downloader.download(request(), tmp_path, _ignore_progress)

    assert raised.value.kind is BackendErrorKind.RETRYABLE
    assert validator.calls == [(source_url, None), (private_url, None)]
    assert opener.urls == [source_url]


async def _ignore_progress(stage: str, percent: float | None) -> None:
    return None
