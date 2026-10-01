import pytest

from app.services.media.registry import MediaRegistry


@pytest.fixture
def registry() -> MediaRegistry:
    return MediaRegistry.default()


@pytest.mark.parametrize(
    ("url", "media_id", "normalized_url"),
    [
        (
            "https://www.instagram.com/reel/DQw4w9WgXcQ/?igsh=abc123",
            "DQw4w9WgXcQ",
            "https://www.instagram.com/reel/DQw4w9WgXcQ/",
        ),
        (
            "https://instagram.com/p/C9fdwM4PnBe/",
            "C9fdwM4PnBe",
            "https://www.instagram.com/p/C9fdwM4PnBe/",
        ),
        (
            "https://m.instagram.com/reels/DQw4w9WgXcQ/",
            "DQw4w9WgXcQ",
            "https://www.instagram.com/reel/DQw4w9WgXcQ/",
        ),
        (
            "https://www.instagram.com/share/reel/BAAbcD_123/?igsh=abc",
            None,
            "https://www.instagram.com/share/reel/BAAbcD_123/",
        ),
    ],
)
def test_registry_parses_supported_instagram_publications(
    registry: MediaRegistry,
    url: str,
    media_id: str | None,
    normalized_url: str,
) -> None:
    parsed = registry.parse(url)

    assert parsed is not None
    assert parsed.source.value == "instagram"
    assert parsed.media_id == media_id
    assert parsed.normalized_url == normalized_url
    adapter = registry.adapter_for(parsed.source)
    metadata = adapter.initial_metadata(parsed)
    assert metadata is not None
    assert metadata.options[0].key == "instagram_bundle"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.instagram.com/reel/DQw4w9WgXcQ/",
        "https://user:pass@www.instagram.com/reel/DQw4w9WgXcQ/",
        "https://www.instagram.com:444/reel/DQw4w9WgXcQ/",
        "https://instagram.com.evil.test/reel/DQw4w9WgXcQ/",
        "https://evil-instagram.com/p/C9fdwM4PnBe/",
        "https://www.instagram.com/stories/public_user/123456789/",
        "https://www.instagram.com/public_user/",
        "https://www.instagram.com/explore/",
        "https://www.instagram.com/share/",
        "https://www.instagram.com/reel/../../etc/passwd",
        "file:///etc/passwd",
    ],
)
def test_registry_rejects_unsafe_or_unsupported_instagram_urls(
    registry: MediaRegistry, url: str
) -> None:
    assert registry.parse(url) is None
