import json

import pytest

from app.services.media.adapters.tiktok import TikTokAdapter
from app.services.media.adapters.youtube import YouTubeAdapter
from app.services.media.errors import MetadataError, MetadataErrorKind, classify_metadata_error
from app.services.media.metadata import YtDlpMetadataService
from app.services.media.models import MediaKind, MediaSource
from app.services.media.registry import MediaRegistry


def test_youtube_metadata_keeps_available_size_compatible_qualities() -> None:
    parsed = MediaRegistry.default().parse("https://youtu.be/dQw4w9WgXcQ")
    assert parsed is not None
    raw = {
        "id": "dQw4w9WgXcQ",
        "title": "Example",
        "duration": 10,
        "thumbnail": "https://img.example/thumb.jpg",
        "formats": [
            {
                "height": 720,
                "vcodec": "avc1.64001F",
                "acodec": "none",
                "ext": "mp4",
                "filesize": 600,
                "fps": 30,
            },
            {
                "height": 1080,
                "vcodec": "avc1.640028",
                "acodec": "none",
                "ext": "mp4",
                "filesize": 2_000,
            },
            {
                "vcodec": "none",
                "acodec": "mp4a.40.2",
                "ext": "m4a",
                "filesize": 100,
            },
        ],
    }

    metadata = YouTubeAdapter().parse_metadata(raw, parsed, max_upload_bytes=1_000)

    assert metadata.source is MediaSource.YOUTUBE
    assert metadata.media_kind is MediaKind.VIDEO
    assert [option.key for option in metadata.options] == ["video_720"]
    assert metadata.options[0].estimated_size == 700


def test_tiktok_metadata_creates_automatic_best_video_option() -> None:
    parsed = MediaRegistry.default().parse(
        "https://www.tiktok.com/@creator/video/7461234567890123456"
    )
    assert parsed is not None
    raw = {
        "id": "7461234567890123456",
        "title": "TikTok example",
        "duration": 15,
        "thumbnail": "https://img.example/tiktok.jpg",
        "formats": [
            {
                "format_id": "download_addr-0",
                "height": 1024,
                "width": 576,
                "vcodec": "h264",
                "acodec": "aac",
                "ext": "mp4",
                "filesize_approx": 900,
                "tbr": 480,
            }
        ],
    }

    metadata = TikTokAdapter().parse_metadata(raw, parsed, max_upload_bytes=1_000)

    assert metadata.source is MediaSource.TIKTOK
    assert metadata.media_id == "7461234567890123456"
    assert metadata.media_kind is MediaKind.VIDEO
    assert len(metadata.options) == 1
    assert metadata.options[0].key == "best_video"
    assert metadata.options[0].label == "Лучшее качество"
    assert metadata.options[0].height == 1024
    assert metadata.options[0].estimated_size == 900


def test_tiktok_video_without_size_or_height_remains_downloadable() -> None:
    parsed = MediaRegistry.default().parse("https://vm.tiktok.com/ZM123abc/")
    assert parsed is not None
    raw = {
        "id": "7461234567890123456",
        "title": "Unknown dimensions",
        "formats": [{"vcodec": "h264", "acodec": "aac", "ext": "mp4"}],
    }

    metadata = TikTokAdapter().parse_metadata(raw, parsed, max_upload_bytes=1_000)

    option = metadata.options[0]
    assert option.height is None
    assert option.estimated_size is None


def test_tiktok_photo_post_is_rejected() -> None:
    parsed = MediaRegistry.default().parse("https://vt.tiktok.com/ZS123abc/")
    assert parsed is not None
    raw = {
        "id": "7461234567890123456",
        "title": "Photo carousel",
        "formats": [],
        "entries": [{"ext": "jpg", "url": "https://img.example/1.jpg"}],
    }

    with pytest.raises(MetadataError) as error:
        TikTokAdapter().parse_metadata(raw, parsed, max_upload_bytes=1_000)

    assert error.value.kind is MetadataErrorKind.UNSUPPORTED_MEDIA


def test_tiktok_video_known_to_exceed_limit_is_rejected() -> None:
    parsed = MediaRegistry.default().parse("https://vm.tiktok.com/ZM123abc/")
    assert parsed is not None
    raw = {
        "id": "7461234567890123456",
        "formats": [
            {
                "height": 720,
                "vcodec": "h264",
                "acodec": "aac",
                "ext": "mp4",
                "filesize": 2_000,
            }
        ],
    }

    with pytest.raises(MetadataError) as error:
        TikTokAdapter().parse_metadata(raw, parsed, max_upload_bytes=1_000)

    assert error.value.kind is MetadataErrorKind.TOO_LARGE


@pytest.mark.parametrize(
    ("stderr", "kind"),
    [
        ("This post requires login. Use --cookies", MetadataErrorKind.AUTHENTICATION),
        ("Video is not available in your country", MetadataErrorKind.RESTRICTED),
        ("Unable to extract webpage video data", MetadataErrorKind.EXTRACTOR_CHANGED),
        ("Video unavailable", MetadataErrorKind.UNAVAILABLE),
    ],
)
def test_tiktok_errors_are_classified(stderr: str, kind: MetadataErrorKind) -> None:
    assert classify_metadata_error(MediaSource.TIKTOK, stderr) is kind


class FakeProcess:
    returncode = 0

    def __init__(self, payload: dict[str, object]) -> None:
        self.payload = payload

    async def communicate(self) -> tuple[bytes, bytes]:
        return json.dumps(self.payload).encode(), b""


@pytest.mark.asyncio
async def test_metadata_service_returns_adapter_metadata(monkeypatch: pytest.MonkeyPatch) -> None:
    payload = {
        "id": "7461234567890123456",
        "title": "From process",
        "formats": [{"vcodec": "h264", "acodec": "aac", "ext": "mp4"}],
    }

    async def fake_subprocess(*args: object, **kwargs: object) -> FakeProcess:
        return FakeProcess(payload)

    monkeypatch.setattr("asyncio.create_subprocess_exec", fake_subprocess)
    registry = MediaRegistry.default()
    parsed = registry.parse("https://vm.tiktok.com/ZM123abc/")
    assert parsed is not None
    service = YtDlpMetadataService(
        registry=registry,
        timeout_seconds=10,
        max_upload_bytes=1_000,
    )

    metadata = await service.fetch(parsed)

    assert metadata.title == "From process"
    assert metadata.options[0].key == "best_video"
