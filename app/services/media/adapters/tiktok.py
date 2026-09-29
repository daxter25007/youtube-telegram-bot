from __future__ import annotations

import re
from urllib.parse import urlunsplit

from app.services.media.adapters.youtube import _split_safe_https_url
from app.services.media.models import MediaSource, ParsedMediaUrl

TIKTOK_CANONICAL_HOSTS = frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com"})
TIKTOK_SHORT_HOSTS = frozenset({"vm.tiktok.com", "vt.tiktok.com"})
CANONICAL_PATH_RE = re.compile(
    r"^/@(?P<account>[A-Za-z0-9._-]{1,64})/video/(?P<media_id>[0-9]{6,32})/?$"
)
SHORT_PATH_RE = re.compile(r"^/[A-Za-z0-9_-]{4,128}/?$")


class TikTokAdapter:
    source = MediaSource.TIKTOK
    automatic_option = "best_video"

    def parse_url(self, text: str) -> ParsedMediaUrl | None:
        parsed = _split_safe_https_url(text)
        if parsed is None:
            return None
        host = (parsed.hostname or "").rstrip(".").lower()
        if host in TIKTOK_SHORT_HOSTS:
            if not SHORT_PATH_RE.fullmatch(parsed.path):
                return None
            return ParsedMediaUrl(
                source=self.source,
                normalized_url=urlunsplit(("https", host, parsed.path, "", "")),
                media_id=None,
            )
        if host not in TIKTOK_CANONICAL_HOSTS:
            return None
        match = CANONICAL_PATH_RE.fullmatch(parsed.path)
        if match is None:
            return None
        account = match.group("account")
        media_id = match.group("media_id")
        return ParsedMediaUrl(
            source=self.source,
            normalized_url=f"https://www.tiktok.com/@{account}/video/{media_id}",
            media_id=media_id,
        )
