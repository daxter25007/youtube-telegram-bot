from __future__ import annotations

from dataclasses import dataclass

from app.services.media.models import MediaSource
from app.services.media.registry import MediaRegistry


@dataclass(frozen=True, slots=True)
class YouTubeURL:
    normalized_url: str
    video_id: str


def validate_youtube_url(raw_url: str) -> YouTubeURL | None:
    parsed = MediaRegistry.default().parse(raw_url)
    if parsed is None or parsed.source is not MediaSource.YOUTUBE or parsed.media_id is None:
        return None
    return YouTubeURL(normalized_url=parsed.normalized_url, video_id=parsed.media_id)
