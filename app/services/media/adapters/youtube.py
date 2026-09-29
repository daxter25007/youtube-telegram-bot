from __future__ import annotations

import re
from urllib.parse import parse_qs, urlsplit, urlunsplit

from app.services.media.models import MediaSource, ParsedMediaUrl

YOUTUBE_HOSTS = frozenset({"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"})
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


class YouTubeAdapter:
    source = MediaSource.YOUTUBE
    automatic_option: str | None = None

    def parse_url(self, text: str) -> ParsedMediaUrl | None:
        parsed = _split_safe_https_url(text)
        if parsed is None:
            return None
        host = (parsed.hostname or "").rstrip(".").lower()
        if host not in YOUTUBE_HOSTS:
            return None

        parts = [part for part in parsed.path.split("/") if part]
        video_id: str | None = None
        if host == "youtu.be" and parts:
            video_id = parts[0]
        elif parsed.path == "/watch":
            values = parse_qs(parsed.query, keep_blank_values=False).get("v")
            video_id = values[0] if values else None
        elif len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
            video_id = parts[1]
        if not video_id or not VIDEO_ID_RE.fullmatch(video_id):
            return None
        return ParsedMediaUrl(
            source=self.source,
            normalized_url=urlunsplit(("https", "www.youtube.com", "/watch", f"v={video_id}", "")),
            media_id=video_id,
        )


def _split_safe_https_url(text: str):
    if not text or text != text.strip():
        return None
    if any(character.isspace() or ord(character) < 32 for character in text):
        return None
    try:
        parsed = urlsplit(text)
        _ = parsed.port
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc:
        return None
    if parsed.username or parsed.password or parsed.port is not None:
        return None
    return parsed
