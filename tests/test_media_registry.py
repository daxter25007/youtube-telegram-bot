import pytest

from app.services.media.models import MediaSource
from app.services.media.registry import MediaRegistry


@pytest.fixture
def registry() -> MediaRegistry:
    return MediaRegistry.default()


@pytest.mark.parametrize(
    ("url", "video_id"),
    [
        ("https://www.youtube.com/watch?v=dQw4w9WgXcQ", "dQw4w9WgXcQ"),
        ("https://youtu.be/dQw4w9WgXcQ?t=5", "dQw4w9WgXcQ"),
        ("https://youtube.com/shorts/dQw4w9WgXcQ", "dQw4w9WgXcQ"),
    ],
)
def test_registry_parses_youtube_urls(registry: MediaRegistry, url: str, video_id: str) -> None:
    parsed = registry.parse(url)

    assert parsed is not None
    assert parsed.source is MediaSource.YOUTUBE
    assert parsed.media_id == video_id
    assert parsed.normalized_url == f"https://www.youtube.com/watch?v={video_id}"


@pytest.mark.parametrize(
    ("url", "media_id"),
    [
        ("https://www.tiktok.com/@creator/video/7461234567890123456", "7461234567890123456"),
        ("https://m.tiktok.com/@creator/video/7461234567890123456?lang=ru", "7461234567890123456"),
        ("https://tiktok.com/@creator/video/7461234567890123456", "7461234567890123456"),
        ("https://vm.tiktok.com/ZM123abc/", None),
        ("https://vt.tiktok.com/ZS123abc/", None),
    ],
)
def test_registry_parses_tiktok_video_urls(
    registry: MediaRegistry, url: str, media_id: str | None
) -> None:
    parsed = registry.parse(url)

    assert parsed is not None
    assert parsed.source is MediaSource.TIKTOK
    assert parsed.media_id == media_id
    if media_id:
        assert parsed.normalized_url == (f"https://www.tiktok.com/@creator/video/{media_id}")
    else:
        assert parsed.normalized_url == url


@pytest.mark.parametrize(
    "url",
    [
        "http://www.tiktok.com/@creator/video/7461234567890123456",
        "https://user:pass@www.tiktok.com/@creator/video/7461234567890123456",
        "https://www.tiktok.com:444/@creator/video/7461234567890123456",
        "https://tiktok.com.evil.test/@creator/video/7461234567890123456",
        "https://evil-tiktok.com/@creator/video/7461234567890123456",
        "https://www.tiktok.com/@creator",
        "https://www.tiktok.com/tag/cats",
        "https://vm.tiktok.com/",
        "https://vt.tiktok.com/\nhttps://example.com",
        "https://127.0.0.1/@creator/video/7461234567890123456",
        "file:///etc/passwd",
        "//www.tiktok.com/@creator/video/7461234567890123456",
    ],
)
def test_registry_rejects_unsafe_or_non_video_urls(registry: MediaRegistry, url: str) -> None:
    assert registry.parse(url) is None


def test_registry_returns_adapter_for_source(registry: MediaRegistry) -> None:
    assert registry.adapter_for(MediaSource.YOUTUBE).source is MediaSource.YOUTUBE
    assert registry.adapter_for(MediaSource.TIKTOK).source is MediaSource.TIKTOK
