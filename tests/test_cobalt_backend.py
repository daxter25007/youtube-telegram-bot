from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.cobalt.client import CobaltClientError, CobaltErrorKind
from app.services.cobalt.models import (
    CobaltMediaResponse,
    CobaltMode,
    CobaltPreparedItem,
    CobaltResponseKind,
)
from app.services.media.backends.base import BackendError, BackendErrorKind, MediaDownloadRequest
from app.services.media.backends.cobalt import CobaltBackend
from app.services.media.models import DownloadPolicy, MediaKind, MediaSource


class FakeCobaltClient:
    base_url = "http://cobalt:9000"

    def __init__(self, *responses: CobaltMediaResponse) -> None:
        self.responses = list(responses)
        self.calls: list[tuple[CobaltMode, bool, str]] = []

    async def prepare(
        self,
        url: str,
        *,
        mode: CobaltMode,
        tiktok_full_audio: bool = False,
        audio_format: str = "best",
    ):
        self.calls.append((mode, tiktok_full_audio, audio_format))
        response = self.responses.pop(0)
        if isinstance(response, Exception):
            raise response
        return response


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


def instagram_request() -> MediaDownloadRequest:
    return MediaDownloadRequest(
        source=MediaSource.INSTAGRAM,
        url="https://www.instagram.com/p/C9fdwM4PnBe/",
        option_key="instagram_bundle",
        policy=DownloadPolicy(format_selector="best"),
    )


def pinterest_request() -> MediaDownloadRequest:
    return MediaDownloadRequest(
        source=MediaSource.PINTEREST,
        url="https://www.pinterest.com/pin/123456789/",
        option_key="pinterest_bundle",
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
    assert client.calls == [
        (CobaltMode.AUTO, True, "best"),
        (CobaltMode.AUDIO, True, "mp3"),
    ]


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
    assert client.calls == [(CobaltMode.AUTO, True, "best")]


@pytest.mark.asyncio
async def test_instagram_prepare_does_not_duplicate_visual_picker_as_audio() -> None:
    visual = CobaltMediaResponse(
        items=(
            item("video_000", MediaKind.VIDEO, "https://cdn/1.mp4", "1.mp4", 0),
            item("photo_000", MediaKind.PHOTO, "https://cdn/2.jpg", "2.jpg", 1),
        )
    )
    repeated_picker = CobaltMediaResponse(
        items=(
            item("video_000", MediaKind.VIDEO, "https://cdn/1.mp4", "1.mp4", 0),
            item("photo_000", MediaKind.PHOTO, "https://cdn/2.jpg", "2.jpg", 1),
        )
    )
    client = FakeCobaltClient(visual, repeated_picker)
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    prepared = await backend.prepare(instagram_request())

    assert [(part.key, part.media_kind) for part in prepared.parts] == [
        ("video_000", MediaKind.VIDEO),
        ("photo_000", MediaKind.PHOTO),
        ("audio", MediaKind.AUDIO),
    ]
    assert [(failure.part_key, failure.error_kind) for failure in prepared.failures] == [
        ("audio", BackendErrorKind.UNSUPPORTED)
    ]
    assert client.calls == [
        (CobaltMode.AUTO, False, "best"),
        (CobaltMode.AUDIO, False, "best"),
    ]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "key", "filename"),
    [
        (MediaKind.PHOTO, "photo_000", "pin.jpg"),
        (MediaKind.ANIMATION, "animation_000", "pin.gif"),
    ],
)
async def test_pinterest_non_video_does_not_request_audio(
    kind: MediaKind,
    key: str,
    filename: str,
) -> None:
    visual = CobaltMediaResponse(items=(item(key, kind, f"https://cdn/{filename}", filename, 0),))
    audio = CobaltMediaResponse(
        items=(item("audio", MediaKind.AUDIO, "https://cdn/audio.m4a", "audio.m4a", 0),)
    )
    client = FakeCobaltClient(visual, audio)
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    prepared = await backend.prepare(pinterest_request())

    assert [(part.key, part.media_kind) for part in prepared.parts] == [(key, kind)]
    assert client.calls == [(CobaltMode.AUTO, False, "best")]


@pytest.mark.asyncio
async def test_pinterest_retries_transient_visual_prepare_once() -> None:
    visual = CobaltMediaResponse(
        items=(item("video", MediaKind.VIDEO, "https://cdn/video.mp4", "video.mp4", 0),)
    )
    audio = CobaltMediaResponse(
        items=(item("audio", MediaKind.AUDIO, "https://cdn/audio.m4a", "audio.m4a", 0),)
    )
    client = FakeCobaltClient(
        CobaltClientError("temporary Pinterest fetch failure", kind=CobaltErrorKind.RETRYABLE),  # type: ignore[arg-type]
        visual,
        audio,
    )
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    prepared = await backend.prepare(pinterest_request())

    assert [(part.key, part.media_kind) for part in prepared.parts] == [
        ("video", MediaKind.VIDEO),
        ("audio", MediaKind.AUDIO),
    ]
    assert client.calls == [
        (CobaltMode.AUTO, False, "best"),
        (CobaltMode.AUTO, False, "best"),
        (CobaltMode.AUDIO, False, "best"),
    ]


@pytest.mark.asyncio
async def test_pinterest_retries_transient_audio_prepare_once() -> None:
    visual = CobaltMediaResponse(
        items=(item("video", MediaKind.VIDEO, "https://cdn/video.mp4", "video.mp4", 0),)
    )
    audio = CobaltMediaResponse(
        items=(item("audio", MediaKind.AUDIO, "https://cdn/audio.m4a", "audio.m4a", 0),)
    )
    client = FakeCobaltClient(
        visual,
        CobaltClientError("temporary Pinterest audio failure", kind=CobaltErrorKind.RETRYABLE),  # type: ignore[arg-type]
        audio,
    )
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )

    prepared = await backend.prepare(pinterest_request())

    assert [(part.key, part.media_kind) for part in prepared.parts] == [
        ("video", MediaKind.VIDEO),
        ("audio", MediaKind.AUDIO),
    ]
    assert prepared.failures == ()
    assert client.calls == [
        (CobaltMode.AUTO, False, "best"),
        (CobaltMode.AUDIO, False, "best"),
        (CobaltMode.AUDIO, False, "best"),
    ]


@pytest.mark.asyncio
async def test_instagram_reel_cover_only_response_triggers_fallback() -> None:
    visual = CobaltMediaResponse(
        items=(item("photo_000", MediaKind.PHOTO, "https://cdn/cover.jpg", "cover.jpg", 0),)
    )
    client = FakeCobaltClient(
        visual,
        CobaltClientError("no audio", kind=CobaltErrorKind.RETRYABLE),  # type: ignore[arg-type]
    )
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )
    request = MediaDownloadRequest(
        source=MediaSource.INSTAGRAM,
        url="https://www.instagram.com/reel/DQw4w9WgXcQ/",
        option_key="instagram_bundle",
        policy=DownloadPolicy(format_selector="best"),
    )

    with pytest.raises(BackendError) as raised:
        await backend.prepare(request)

    assert raised.value.kind is BackendErrorKind.RETRYABLE
    assert client.calls == [(CobaltMode.AUTO, False, "best")]


@pytest.mark.asyncio
async def test_audio_prepare_failure_keeps_visual_part() -> None:
    visual = CobaltMediaResponse(
        items=(item("video", MediaKind.VIDEO, "https://cdn/video.mp4", "video.mp4", 0),)
    )
    client = FakeCobaltClient(
        visual,
        CobaltClientError("audio failed", kind=CobaltErrorKind.RETRYABLE),  # type: ignore[arg-type]
    )
    backend = CobaltBackend(
        client=client,  # type: ignore[arg-type]
        validator=FakeValidator(),  # type: ignore[arg-type]
        opener=FakeOpener(FakeResponse([b"video"], headers={"Content-Type": "video/mp4"})),  # type: ignore[arg-type]
        max_upload_bytes=100,
    )
    prepared = await backend.prepare(request())

    assert [part.key for part in prepared.parts] == ["video", "audio"]
    assert [(failure.part_key, failure.error_kind) for failure in prepared.failures] == [
        ("audio", BackendErrorKind.RETRYABLE)
    ]


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


@pytest.mark.asyncio
@pytest.mark.parametrize("audio_headers", [{"Content-Length": "0"}, {}])
async def test_empty_cobalt_audio_is_reported_as_failed_part(
    tmp_path: Path,
    audio_headers: dict[str, str],
) -> None:
    visual = CobaltMediaResponse(
        items=(
            item("photo_000", MediaKind.PHOTO, "https://cdn/1.jpg", "one.jpg", 0),
            item("audio", MediaKind.AUDIO, "https://cdn/a.m4a", "sound.m4a", 1),
        )
    )
    opener = FakeOpener(
        FakeResponse([b"photo"], headers={"Content-Length": "5", "Content-Type": "image/jpeg"}),
        FakeResponse([], headers=audio_headers),
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

    assert [artifact.part_key for artifact in package.artifacts] == ["photo_000"]
    assert [(failure.part_key, failure.error_kind) for failure in package.failures] == [
        ("audio", BackendErrorKind.RETRYABLE)
    ]
    assert not (tmp_path / "audio.part").exists()
    assert not (tmp_path / "audio.m4a").exists()
