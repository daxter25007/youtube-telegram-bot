from __future__ import annotations

import re
from urllib.parse import urlunsplit

from app.services.media.adapters.youtube import YouTubeAdapter, _split_safe_https_url
from app.services.media.models import DownloadPolicy, MediaKind, MediaSource, ParsedMediaUrl

RUTUBE_HOSTS = frozenset({"rutube.ru", "www.rutube.ru", "m.rutube.ru"})
RUTUBE_VIDEO_ID_RE = re.compile(r"^[a-z0-9]{32}$")


class RutubeAdapter(YouTubeAdapter):
    source = MediaSource.RUTUBE
    platform_name = "Rutube"
    fallback_title = "Rutube video"

    def download_policy(self, option_key: str) -> DownloadPolicy:
        if option_key == "audio_m4a":
            return DownloadPolicy(
                format_selector="bestaudio[ext=m4a]/bestaudio/best",
                media_kind=MediaKind.AUDIO,
                extract_audio=True,
                audio_format="m4a",
            )
        return super().download_policy(option_key)

    def parse_url(self, text: str) -> ParsedMediaUrl | None:
        parsed = _split_safe_https_url(text)
        if parsed is None:
            return None
        host = (parsed.hostname or "").rstrip(".").lower()
        if host not in RUTUBE_HOSTS:
            return None

        parts = [part for part in parsed.path.split("/") if part]
        video_id: str | None = None
        if len(parts) == 2 and parts[0] in {"video", "shorts", "embed"}:
            video_id = parts[1]
        elif len(parts) == 3 and parts[:2] == ["play", "embed"]:
            video_id = parts[2]
        if not video_id or not RUTUBE_VIDEO_ID_RE.fullmatch(video_id):
            return None

        return ParsedMediaUrl(
            source=self.source,
            normalized_url=urlunsplit(("https", "rutube.ru", f"/video/{video_id}/", "", "")),
            media_id=video_id,
        )
