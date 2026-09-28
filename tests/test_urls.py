from app.utils.urls import validate_youtube_url


def test_accepts_supported_youtube_urls() -> None:
    for url in (
        "https://www.youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtu.be/dQw4w9WgXcQ?t=1",
        "https://youtube.com/shorts/dQw4w9WgXcQ",
        "https://m.youtube.com/watch?v=dQw4w9WgXcQ&feature=share",
    ):
        parsed = validate_youtube_url(url)
        assert parsed is not None
        assert parsed.video_id == "dQw4w9WgXcQ"
        assert parsed.normalized_url == "https://www.youtube.com/watch?v=dQw4w9WgXcQ"


def test_rejects_malicious_and_non_youtube_urls() -> None:
    for url in (
        "file:///etc/passwd",
        "ftp://youtube.com/watch?v=dQw4w9WgXcQ",
        "http://localhost/watch?v=dQw4w9WgXcQ",
        "http://127.0.0.1/watch?v=dQw4w9WgXcQ",
        "http://0.0.0.0/watch?v=dQw4w9WgXcQ",
        "https://youtube.com.evil.test/watch?v=dQw4w9WgXcQ",
        "https://evil.test/?next=https://youtube.com/watch?v=dQw4w9WgXcQ",
        "https://user:pass@youtube.com/watch?v=dQw4w9WgXcQ",
        "https://youtube.com:444/watch?v=dQw4w9WgXcQ",
        "https://youtube.com/watch?v=../../etc/passwd",
        "javascript:alert(1)",
    ):
        assert validate_youtube_url(url) is None
