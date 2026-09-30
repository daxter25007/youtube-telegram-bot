from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.cobalt.models import (
    CobaltMediaResponse,
    CobaltMode,
    CobaltPreparedItem,
    CobaltResponseKind,
)
from app.services.media.backends.base import BackendErrorKind, MediaDownloadRequest
from app.services.media.backends.cobalt import CobaltBackend
from app.services.media.models import DownloadPolicy, MediaKind, MediaSource


class FakeCobaltClient:
    base_url = "http://cobalt:9000"

    def __init__(self, *responses: CobaltMediaResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[CobaltMode, bool]] = []

    async def prepare(self, url: str, *, mode: CobaltMode, tiktok_full_audio: bool = False):
        self.calls.append((mode, tiktok_full_audio))
        return self.responses.pop(0)


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


class FakeValidator:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str | None]] = []

    async def validate(self, url: str, *, allowed_internal_origin=None):
        self.calls.append((url, allowed_internal_origin))
        return SimpleNamespace(url=url, is_internal=allowed_internal_origin is not None)


def item(
    key: str,
    kind: MediaKind,
    url: str,
    filename: str,
    position: int,
) -> CobaltPreparedItem:
    return CobaltPreparedItem(
        part_key=key,
        media_kind=kind,
        position=position,
        url=url,
        filename=filename,
        response_kind=CobaltResponseKind.REDIRECT,
    )


def request() -> MediaDownloadRequest:
    return MediaDownloadRequest(
        source=MediaSource.TIKTOK,
        url="https://www.tiktok.com/@creator/video/7461234567890123456",
        option_key="tiktok_bundle",
        policy=DownloadPolicy(format_selector="best"),
    )


async def ignore_progress(stage: str, percent: float | None) -> None:
    return None


@pytest.mark.asyncio
async def test_prepare_combines_visuals_with_separate_full_audio() -> None:
    visual = CobaltMediaResponse(
        items=(item("video", MediaKind.VIDEO, "https://cdn/video.mp4", "video.mp4", 0),)
    )
    audio = CobaltMediaResponse(
        items=(item("audio", MediaKind.AUDIO, "https://cdn/audio.opus", "audio.opus", 0),)
    )
    client = FakeCobaltClient(visual, audio)
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    prepared = await backend.prepare(request())

    assert [(part.key, part.media_kind, part.position) for part in prepared.parts] == [
        ("video", MediaKind.VIDEO, 0),
        ("audio", MediaKind.AUDIO, 1),
    ]
    assert client.calls == [(CobaltMode.AUTO, False), (CobaltMode.AUDIO, True)]


@pytest.mark.asyncio
async def test_prepare_reuses_picker_audio_without_second_request() -> None:
    visual = CobaltMediaResponse(
        items=(
            item("photo_000", MediaKind.PHOTO, "https://cdn/1.jpg", "1.jpg", 0),
            item("audio", MediaKind.AUDIO, "https://cdn/audio.m4a", "audio.m4a", 1),
        )
    )
    client = FakeCobaltClient(visual)
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    prepared = await backend.prepare(request())

    assert [part.key for part in prepared.parts] == ["photo_000", "audio"]
    assert client.calls == [(CobaltMode.AUTO, False)]


@pytest.mark.asyncio
async def test_download_prepared_streams_only_selected_parts_to_unique_paths(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    visual = CobaltMediaResponse(
        items=(
            item("photo_000", MediaKind.PHOTO, "https://cdn/1.jpg", "one.jpg", 0),
            item("photo_001", MediaKind.PHOTO, "https://cdn/2.jpg", "two.jpg", 1),
            item("audio", MediaKind.AUDIO, "https://cdn/a.opus", "sound.opus", 2),
        )
    )
    opener = FakeOpener(
        FakeResponse([b"photo"], headers={"Content-Length": "5", "Content-Type": "image/jpeg"}),
        FakeResponse([b"audio"], headers={"Content-Length": "5", "Content-Type": "audio/ogg"}),
    )
    backend = CobaltBackend(
        client=FakeCobaltClient(visual),  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=opener,  # type: ignore[arg-type]
        max_upload_bytes=100,
    )
    prepared = await backend.prepare(request())

    package = await backend.download_prepared(
        prepared,
        frozenset({"photo_001", "audio"}),
        tmp_path,
        ignore_progress,
    )

    assert [(artifact.part_key, artifact.path.name) for artifact in package.artifacts] == [
        ("photo_001", "photo_001.jpg"),
        ("audio", "audio.opus"),
    ]
    assert opener.urls == ["https://cdn/2.jpg", "https://cdn/a.opus"]
    assert package.failures == ()


@pytest.mark.asyncio
async def test_oversized_part_does_not_suppress_smaller_part(tmp_path: Path) -> None:
    visual = CobaltMediaResponse(
        items=(
            item("photo_000", MediaKind.PHOTO, "https://cdn/1.jpg", "one.jpg", 0),
            item("audio", MediaKind.AUDIO, "https://cdn/a.m4a", "sound.m4a", 1),
        )
    )
    opener = FakeOpener(
        FakeResponse([], headers={"Content-Length": "101", "Content-Type": "image/jpeg"}),
        FakeResponse([b"ok"], headers={"Content-Length": "2", "Content-Type": "audio/mp4"}),
    )
    backend = CobaltBackend(
        client=FakeCobaltClient(visual),  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=opener,  # type: ignore[arg-type]
        max_upload_bytes=100,
    )
    prepared = await backend.prepare(request())

    package = await backend.download_prepared(
        prepared,
        frozenset({"photo_000", "audio"}),
        tmp_path,
        ignore_progress,
    )

    assert [artifact.part_key for artifact in package.artifacts] == ["audio"]
    assert [(failure.part_key, failure.error_kind) for failure in package.failures] == [
        ("photo_000", BackendErrorKind.TOO_LARGE)
    ]
    assert not (tmp_path / "photo_000.part").exists()
