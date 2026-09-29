from types import SimpleNamespace

import pytest

from app.services.cobalt.client import CobaltClient, CobaltClientError, CobaltErrorKind
from app.services.cobalt.models import CobaltResponseKind


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
    def __init__(self, response: FakeResponse) -> None:
        self.response = response
        self.posts: list[tuple[str, dict[str, object]]] = []

    async def post(self, url: str, **kwargs):
        self.posts.append((url, kwargs))
        return self.response


class FakeValidator:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[tuple[str, str | None]] = []
        self.error = error

    async def validate(self, url: str, *, allowed_internal_origin: str | None = None):
        self.calls.append((url, allowed_internal_origin))
        if self.error is not None:
            raise self.error
        return SimpleNamespace(url=url)


def client_for(payload: object, *, status: int = 200):
    response = FakeResponse(payload, status=status)
    session = FakeSession(response)
    validator = FakeValidator()
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

    result = await client.prepare("https://www.tiktok.com/@creator/video/7461234567890123456")

    assert result.kind is CobaltResponseKind(status)
    assert result.filename == "video.mp4"
    body = session.posts[0][1]["json"]
    assert body["alwaysProxy"] is True
    assert body["allowH265"] is False
    expected_origin = "http://cobalt:9000" if status == "tunnel" else None
    assert validator.calls == [(result.url, expected_origin)]
    assert response.released is True


@pytest.mark.asyncio
async def test_client_selects_video_from_picker() -> None:
    client, _, validator, _ = client_for(
        {
            "status": "picker",
            "picker": [
                {"type": "photo", "url": "https://cdn.example/photo.jpg"},
                {"type": "video", "url": "https://cdn.example/video.mp4"},
            ],
        }
    )

    result = await client.prepare("https://vm.tiktok.com/ABC123/")

    assert result.kind is CobaltResponseKind.PICKER
    assert result.url == "https://cdn.example/video.mp4"
    assert validator.calls == [(result.url, None)]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("payload", "kind"),
    [
        (
            {"status": "picker", "picker": [{"type": "photo", "url": "https://x/y"}]},
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
        await client.prepare("https://vm.tiktok.com/ABC123/")

    assert raised.value.kind is kind


@pytest.mark.asyncio
async def test_client_rejects_invalid_json_without_leaking_url() -> None:
    client, _, _, _ = client_for(ValueError("invalid json"))
    source_url = "https://vm.tiktok.com/SECRET123/"

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare(source_url)

    assert source_url not in str(raised.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("error", [TimeoutError(), OSError("connection reset")])
async def test_client_normalizes_json_read_failures_for_backend_fallback(error: Exception) -> None:
    client, _, _, response = client_for(error)

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare("https://vm.tiktok.com/ABC123/")

    assert raised.value.kind is CobaltErrorKind.RETRYABLE
    assert response.released is True


@pytest.mark.asyncio
async def test_client_normalizes_validator_dns_failure_for_backend_fallback() -> None:
    response = FakeResponse({"status": "redirect", "url": "https://cdn.example/video.mp4"})
    validator = FakeValidator(OSError("dns failed"))
    client = CobaltClient(
        base_url="http://cobalt:9000",
        request_timeout_seconds=30,
        session=FakeSession(response),  # type: ignore[arg-type]
        validator=validator,  # type: ignore[arg-type]
    )

    with pytest.raises(CobaltClientError) as raised:
        await client.prepare("https://vm.tiktok.com/ABC123/")

    assert raised.value.kind is CobaltErrorKind.RETRYABLE
    assert response.released is True
