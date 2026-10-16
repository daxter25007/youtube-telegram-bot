import pytest

from app.services.media.adapters.rutube import RutubeAdapter
from app.services.media.models import MediaKind, MediaSource
from app.services.media.registry import MediaRegistry


def test_rutube_metadata_builds_youtube_style_quality_and_audio_options() -> None:
    parsed = MediaRegistry.default().parse(
        "https://rutube.ru/shorts/3eac3b4561676c17df9132a9a1e62e3e/"
    )
    assert parsed is not None
    raw = {
        "id": "3eac3b4561676c17df9132a9a1e62e3e",
        "title": "Rutube example",
        "duration": 10,
        "thumbnail": "https://pic.rutubelist.ru/video/thumb.jpg",
        "formats": [
            {
                "height": 360,
                "vcodec": "avc1.42c01f",
                "acodec": "mp4a.40.2",
                "ext": "mp4",
                "tbr": 300,
                "fps": 30,
            },
            {
                "height": 720,
                "vcodec": "avc1.640029",
                "acodec": "mp4a.40.2",
                "ext": "mp4",
                "tbr": 700,
                "fps": 30,
            },
        ],
    }

    metadata = RutubeAdapter().parse_metadata(raw, parsed, max_upload_bytes=2_000_000)

    assert metadata.source is MediaSource.RUTUBE
    assert metadata.source_url == ("https://rutube.ru/video/3eac3b4561676c17df9132a9a1e62e3e/")
    assert metadata.media_kind is MediaKind.VIDEO
    assert [option.key for option in metadata.options] == [
        "video_360",
        "video_720",
        "audio_m4a",
    ]
    assert metadata.options[-1].label == "🎵 Только аудио"
    assert metadata.options[-1].media_kind is MediaKind.AUDIO


@pytest.mark.parametrize("option_key", ["video_360", "video_720"])
def test_rutube_video_option_uses_quality_selector(option_key: str) -> None:
    policy = RutubeAdapter().download_policy(option_key)

    assert f"height<={option_key.removeprefix('video_')}" in policy.format_selector
    assert policy.media_kind is MediaKind.VIDEO


def test_rutube_audio_option_extracts_m4a() -> None:
    policy = RutubeAdapter().download_policy("audio_m4a")

    assert policy.media_kind is MediaKind.AUDIO
    assert policy.extract_audio is True
    assert policy.audio_format == "m4a"
    assert policy.format_selector == "bestaudio[ext=m4a]/bestaudio/best"
