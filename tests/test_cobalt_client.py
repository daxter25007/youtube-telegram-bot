from types import SimpleNamespace

import pytest

from app.services.cobalt.client import CobaltClient, CobaltClientError, CobaltErrorKind
from app.services.cobalt.models import CobaltMode, CobaltResponseKind
from app.services.cobalt.security import SafeRemoteUrlValidator, UnsafeRemoteUrlError
from app.services.media.models import MediaKind


class FakeResponse:
    def __init__(self, payload: object, *, status: int = 200) -> None:
        self.payload = payload
        self.status = status
        self.released = False

    async def json(self, *, content_type=None):
        if isinstance(self.payload, Exception):
            raise self.payload
        return self.payload

    def release(self) -> None:
        self.released = True


class FakeSession:
    def __init__(self, *responses: FakeResponse) -> None:
        self.responses = list(responses)
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, **kwargs):
        self.posts.append((url, kwargs))
        return self.responses.pop(0)


class FakeValidator:
    def __init__(self, rejected: set[str] | None = None, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.rejected = rejected or set()
        self.error = error

    async def validate(self, url: str, *, allowed_internal_origin: str | None = None):
        self.calls.append((url, allowed_internal_origin))
        if url in self.rejected:
            raise UnsafeRemoteUrlError("unsafe")
        if self.error is not None:
            raise self.error
        return SimpleNamespace(url=url)


def client_for(payload: object, *, status: int = 200, validator=None):
    response = FakeResponse(payload, status=status)
    session = FakeSession(response)
    validator = validator or FakeValidator()
    client = CobaltClient(
        base_url="http://cobalt:9000",
        request_timeout_seconds=30,
        session=session,  # type: ignore[arg-type]
        validator=validator,  # type: ignore[arg-type]
    )
    return client, session, validator, response


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["tunnel", "redirect"])
async def test_client_parses_single_video_response(status: str) -> None:
    client, session, validator, response = client_for(
        {"status": status, "url": "http://cobalt:9000/tunnel?id=1", "filename": "video.mp4"}
    )

    result = await client.prepare(
        "https://www.tiktok.com/@creator/video/7461234567890123456",
        mode=CobaltMode.AUTO,
    )

    assert len(result.items) == 1
    item = result.items[0]
    assert item.response_kind is CobaltResponseKind(status)
    assert item.filename == "video.mp4"
    assert item.part_key == "video"
    assert item.media_kind is MediaKind.VIDEO
    body = session.posts[0][1]["json"]
    assert body["downloadMode"] == "auto"
    assert body["alwaysProxy"] is True
    assert body["allowH265"] is False
    assert body["audioFormat"] == "best"
    expected_origin = "http://cobalt:9000" if status == "tunnel" else None
    assert validator.calls == [(item.url, expected_origin)]
    assert response.released is True


@pytest.mark.asyncio
async def test_client_preserves_ordered_photos_and_picker_audio() -> None:
    client, _, validator, _ = client_for(
        {
            "status": "picker",
            "picker": [
                {"type": "photo", "url": "https://cdn.example/1.jpg"},
                {"type": "photo", "url": "https://cdn.example/2.webp"},
            ],
            "audio": "https://cdn.example/sound.m4a",
            "audioFilename": "sound.m4a",
        }
    )

    result = await client.prepare("https://vm.tiktok.com/ABC123/", mode=CobaltMode.AUTO)

    assert [(item.part_key, item.media_kind, item.position) for item in result.items] == [
        ("photo_000", MediaKind.PHOTO, 0),
        ("photo_001", MediaKind.PHOTO, 1),
        ("audio", MediaKind.AUDIO, 2),
    ]
    assert result.items[2].filename == "sound.m4a"
    assert validator.calls == [
        ("https://cdn.example/1.jpg", "http://cobalt:9000"),
        ("https://cdn.example/2.webp", "http://cobalt:9000"),
        ("https://cdn.example/sound.m4a", "http://cobalt:9000"),
    ]
    body = client.session.posts[0][1]["json"]  # type: ignore[attr-defined]
    assert body["audioFormat"] == "best"


@pytest.mark.asyncio
async def test_client_assigns_unique_keys_to_mixed_picker_videos() -> None:
    client, _, _, _ = client_for(
        {
            "status": "picker",
            "picker": [
                {"type": "video", "url": "https://cdn.example/1.mp4"},
                {"type": "photo", "url": "https://cdn.example/2.jpg"},
                {"type": "video", "url": "https://cdn.example/3.mp4"},
            ],
        }
    )

    result = await client.prepare("https://www.instagram.com/p/C9fdwM4PnBe/", mode=CobaltMode.AUTO)

    assert [(item.part_key, item.media_kind, item.position) for item in result.items] == [
        ("video_000", MediaKind.VIDEO, 0),
        ("photo_000", MediaKind.PHOTO, 1),
        ("video_001", MediaKind.VIDEO, 2),
    ]


@pytest.mark.asyncio
async def test_client_recognizes_single_photo_response_from_filename() -> None:
    client, _, _, _ = client_for(
        {
            "status": "redirect",
            "url": "https://cdn.example/photo.jpg",
            "filename": "instagram_C9fdwM4PnBe.jpg",
        }
    )

    result = await client.prepare("https://www.instagram.com/p/C9fdwM4PnBe/", mode=CobaltMode.AUTO)

    assert result.items[0].part_key == "photo_000"
    assert result.items[0].media_kind is MediaKind.PHOTO


@pytest.mark.asyncio
async def test_audio_mode_requests_best_full_tiktok_audio() -> None:
    client, session, _, _ = client_for(
        {"status": "redirect", "url": "https://cdn.example/sound.opus", "filename": "sound.opus"}
    )

    result = await client.prepare(
        "https://vm.tiktok.com/ABC123/",
        mode=CobaltMode.AUDIO,
        tiktok_full_audio=True,
    )

    body = session.posts[0][1]["json"]
    assert body["downloadMode"] == "audio"
    assert body["audioFormat"] == "best"
    assert body["tiktokFullAudio"] is True
    assert result.items[0].part_key == "audio"
    assert result.items[0].media_kind is MediaKind.AUDIO
    assert result.items[0].filename == "sound.opus"


@pytest.mark.asyncio
async def test_client_rejects_unsafe_item_in_picker_before_download() -> None:
    validator = FakeValidator({"http://127.0.0.1/private.jpg"})
    client, _, _, _ = client_for(
        {
            "status": "picker",
            "picker": [
                {"type": "photo", "url": "https://cdn.example/1.jpg"},
                {"type": "photo", "url": "http://127.0.0.1/private.jpg"},
            ],
        },
        validator=validator,
    )

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare("https://vm.tiktok.com/ABC123/", mode=CobaltMode.AUTO)

    assert raised.value.kind is CobaltErrorKind.SECURITY


@pytest.mark.asyncio
async def test_picker_allows_only_configured_internal_cobalt_origin() -> None:
    async def public_resolver(host: str, port: int) -> tuple[str, ...]:
        return ("93.184.216.34",)

    validator = SafeRemoteUrlValidator(public_resolver)
    client, _, _, _ = client_for(
        {
            "status": "picker",
            "picker": [{"type": "photo", "url": "http://cobalt:9000/tunnel?id=1"}],
            "audio": "http://127.0.0.1/private.m4a",
        },
        validator=validator,
    )

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare("https://vm.tiktok.com/ABC123/", mode=CobaltMode.AUTO)

    assert raised.value.kind is CobaltErrorKind.SECURITY


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (
            {"status": "picker", "picker": [{"type": "unknown", "url": "https://x/y"}]},
            CobaltErrorKind.UNSUPPORTED,
        ),
        ({"status": "error", "error": {"code": "error.api.fetch"}}, CobaltErrorKind.RETRYABLE),
        ({"status": "local-processing", "tunnel": []}, CobaltErrorKind.RETRYABLE),
        ({"status": "unknown"}, CobaltErrorKind.RETRYABLE),
        ({"status": "tunnel"}, CobaltErrorKind.RETRYABLE),
    ],
)
async def test_client_rejects_unsupported_or_invalid_responses(
    payload: object,
    kind: CobaltErrorKind,
) -> None:
    client, _, _, _ = client_for(payload)

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare("https://vm.tiktok.com/ABC123/", mode=CobaltMode.AUTO)

    assert raised.value.kind is kind


@pytest.mark.asyncio
async def test_client_rejects_invalid_json_without_leaking_url() -> None:
    client, _, _, _ = client_for(ValueError("invalid json"))
    source_url = "https://vm.tiktok.com/SECRET123/"

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare(source_url, mode=CobaltMode.AUTO)

    assert source_url not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), OSError("connection reset")])
async def test_client_normalizes_json_read_failures_for_backend_fallback(error: Exception) -> None:
    client, _, _, response = client_for(error)

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare("https://vm.tiktok.com/ABC123/", mode=CobaltMode.AUTO)

    assert raised.value.kind is CobaltErrorKind.RETRYABLE
    assert response.released is True


@pytest.mark.asyncio
async def test_client_normalizes_validator_dns_failure_for_backend_fallback() -> None:
    response = FakeResponse({"status": "redirect", "url": "https://cdn.example/video.mp4"})
    validator = FakeValidator(error=OSError("dns failed"))
    client = CobaltClient(
        base_url="http://cobalt:9000",
        request_timeout_seconds=30,
        session=FakeSession(response),  # type: ignore[arg-type]
        validator=validator,  # type: ignore[arg-type]
    )

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare("https://vm.tiktok.com/ABC123/", mode=CobaltMode.AUTO)

    assert raised.value.kind is CobaltErrorKind.RETRYABLE
    assert response.released is True
