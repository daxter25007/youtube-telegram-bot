import pytest

from app.services.media.models import MediaSource
from app.services.media.registry import MediaRegistry


@pytest.fixture
def registry() -> MediaRegistry:
    return MediaRegistry.default()


@pytest.mark.parametrize(
    ("url", "media_id", "normalized_url"),
    [
        (
            "https://vk.com/video-123456_789012?list=ln-AbC123",
            "-123456_789012",
            "https://vk.com/video-123456_789012?list=ln-AbC123",
        ),
        (
            "https://m.vk.com/clip123456_789012?from=clips",
            "123456_789012",
            "https://vk.com/clip123456_789012",
        ),
        (
            "https://vkvideo.ru/video-123456_789012",
            "-123456_789012",
            "https://vk.com/video-123456_789012",
        ),
        (
            "https://vk.ru/clip-123456_789012",
            "-123456_789012",
            "https://vk.com/clip-123456_789012",
        ),
        (
            "https://vk.com/clips-123456?z=clip-123456_789012%2Fclub123456",
            "-123456_789012",
            "https://vk.com/clip-123456_789012",
        ),
        (
            "https://vk.com/videos-123456?z=video-123456_789012%2Fpl_-123456_-2",
            "-123456_789012",
            "https://vk.com/video-123456_789012",
        ),
    ],
)
def test_registry_parses_supported_vk_video_urls(
    registry: MediaRegistry,
    url: str,
    media_id: str,
    normalized_url: str,
) -> None:
    parsed = registry.parse(url)

    assert parsed is not None
    assert parsed.source is MediaSource.VK
    assert parsed.media_id == media_id
    assert parsed.normalized_url == normalized_url
    metadata = registry.adapter_for(parsed.source).initial_metadata(parsed)
    assert metadata.options[0].key == "vk_video"


@pytest.mark.parametrize(
    "url",
    [
        "http://vk.com/video-123456_789012",
        "https://user:pass@vk.com/video-123456_789012",
        "https://vk.com:444/video-123456_789012",
        "https://vk.com.evil.test/video-123456_789012",
        "https://evil-vk.com/video-123456_789012",
        "https://vk.com/id123456",
        "https://vk.com/videos-123456",
        "https://vk.com/video-123456_789012/extra",
        "https://vk.com/video_ext.php?oid=-123456&id=789012",
        "https://vk.com/clips-123456?z=../../etc/passwd",
        "https://vk.com/video-123456_789012\nhttps://example.com",
        "file:///etc/passwd",
    ],
)
def test_registry_rejects_unsafe_or_unsupported_vk_urls(registry: MediaRegistry, url: str) -> None:
    assert registry.parse(url) is None


def test_vk_adapter_builds_video_download_policy(registry: MediaRegistry) -> None:
    adapter = registry.adapter_for(MediaSource.VK)

    policy = adapter.download_policy("vk_video")

    assert policy.format_selector == "best[ext=mp4]/best"
