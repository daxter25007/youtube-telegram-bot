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
            "https://www.pinterest.com/pin/123456789012345678/?utm_source=share",
            "123456789012345678",
            "https://www.pinterest.com/pin/123456789012345678/",
        ),
        (
            "https://ru.pinterest.com/pin/abc_DEF-123/story-title/",
            "abc_DEF-123",
            "https://www.pinterest.com/pin/abc_DEF-123/",
        ),
        (
            "https://pin.it/AbC123xy?utm_source=share",
            None,
            "https://pin.it/AbC123xy",
        ),
    ],
)
def test_registry_parses_supported_pinterest_pins(
    registry: MediaRegistry,
    url: str,
    media_id: str | None,
    normalized_url: str,
) -> None:
    parsed = registry.parse(url)

    assert parsed is not None
    assert parsed.source is MediaSource.PINTEREST
    assert parsed.media_id == media_id
    assert parsed.normalized_url == normalized_url
    metadata = registry.adapter_for(parsed.source).initial_metadata(parsed)
    assert metadata.options[0].key == "pinterest_bundle"


@pytest.mark.parametrize(
    "url",
    [
        "http://www.pinterest.com/pin/123456789/",
        "https://user:pass@www.pinterest.com/pin/123456789/",
        "https://www.pinterest.com:444/pin/123456789/",
        "https://pinterest.com.evil.test/pin/123456789/",
        "https://evil-pinterest.com/pin/123456789/",
        "https://www.pinterest.com/example/board-name/",
        "https://www.pinterest.com/search/pins/?q=cats",
        "https://www.pinterest.com/pin/../../etc/passwd",
        "https://pin.it/",
        "https://pin.it/abc/extra",
        "file:///etc/passwd",
    ],
)
def test_registry_rejects_unsafe_or_unsupported_pinterest_urls(
    registry: MediaRegistry, url: str
) -> None:
    assert registry.parse(url) is None
