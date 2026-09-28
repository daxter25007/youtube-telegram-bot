import pytest

from app.services.youtube.formats import build_format_selector, select_quality_options


def test_quality_selection_prefers_mp4_h264_and_filters_large() -> None:
    formats = [
        {
            "format_id": "v1",
            "height": 720,
            "vcodec": "vp9",
            "acodec": "none",
            "ext": "webm",
            "filesize": 500,
            "fps": 30,
        },
        {
            "format_id": "v2",
            "height": 720,
            "vcodec": "avc1.64001F",
            "acodec": "none",
            "ext": "mp4",
            "filesize": 600,
            "fps": 30,
        },
        {
            "format_id": "v3",
            "height": 1080,
            "vcodec": "avc1.640028",
            "acodec": "none",
            "ext": "mp4",
            "filesize": 2000,
        },
        {
            "format_id": "a1",
            "height": None,
            "vcodec": "none",
            "acodec": "mp4a.40.2",
            "ext": "m4a",
            "filesize": 100,
        },
    ]
    options = select_quality_options(formats, duration=10, max_upload_bytes=1000)
    assert [option.height for option in options] == [720]
    assert options[0].codec == "avc1.64001F"
    assert options[0].estimated_size == 700


def test_format_selector_is_characteristic_based() -> None:
    selector = build_format_selector(1080)
    assert "height<=1080" in selector
    assert "vcodec^=avc" in selector
    assert "format_id" not in selector
    with pytest.raises(ValueError):
        build_format_selector(999)
