from __future__ import annotations

import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit, urlunsplit

ALLOWED_HOSTS = {"youtube.com", "www.youtube.com", "m.youtube.com", "youtu.be"}
VIDEO_ID_RE = re.compile(r"^[A-Za-z0-9_-]{6,64}$")


@dataclass(frozen=True, slots=True)
class YouTubeURL:
    normalized_url: str
    video_id: str


def validate_youtube_url(raw_url: str) -> YouTubeURL | None:
    candidate = raw_url.strip()
    if not candidate or any(ord(char) < 32 for char in candidate):
        return None
    try:
        parsed = urlsplit(candidate)
        host = (parsed.hostname or "").rstrip(".").lower()
    except ValueError:
        return None
    if parsed.scheme not in {"http", "https"} or host not in ALLOWED_HOSTS:
        return None
    if parsed.username or parsed.password or parsed.port not in (None, 80, 443):
        return None

    video_id: str | None = None
    parts = [part for part in parsed.path.split("/") if part]
    if host == "youtu.be" and parts:
        video_id = parts[0]
    elif parsed.path == "/watch":
        values = parse_qs(parsed.query, keep_blank_values=False).get("v")
        video_id = values[0] if values else None
    elif len(parts) >= 2 and parts[0] in {"shorts", "live", "embed"}:
        video_id = parts[1]

    if not video_id or not VIDEO_ID_RE.fullmatch(video_id):
        return None
    normalized = urlunsplit(("https", "www.youtube.com", "/watch", f"v={video_id}", ""))
    return YouTubeURL(normalized_url=normalized, video_id=video_id)
