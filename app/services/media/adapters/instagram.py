from __future__ import annotations

import hashlib
import re
from typing import Any
from urllib.parse import urlunsplit

from app.services.media.adapters.youtube import _split_safe_https_url
from app.services.media.errors import MetadataError, MetadataErrorKind
from app.services.media.formats import best_video_format, format_size
from app.services.media.models import (
    DownloadOption,
    DownloadPolicy,
    MediaKind,
    MediaMetadata,
    MediaSource,
    ParsedMediaUrl,
)

INSTAGRAM_HOSTS = frozenset({"instagram.com", "www.instagram.com", "m.instagram.com"})
PUBLICATION_PATH_RE = re.compile(r"^/(?P<kind>reel|reels|p)/(?P<media_id>[A-Za-z0-9_-]{5,64})/?$")
SHARE_PATH_RE = re.compile(r"^/share/(?P<share_id>(?:(?:reel|p)/)?[A-Za-z0-9_-]{5,128})/?$")


class InstagramAdapter:
    source = MediaSource.INSTAGRAM
    automatic_option = "instagram_bundle"

    def initial_metadata(self, parsed: ParsedMediaUrl) -> MediaMetadata:
        media_id = parsed.media_id
        if media_id is None:
            digest = hashlib.sha256(parsed.normalized_url.encode("utf-8")).hexdigest()[:32]
            media_id = f"url_{digest}"
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title="Instagram publication",
            duration=None,
            thumbnail=None,
            media_kind=MediaKind.VIDEO,
            options=(
                DownloadOption(
                    key=self.automatic_option,
                    label="Скачать публикацию",
                    media_kind=MediaKind.VIDEO,
                ),
            ),
        )

    def parse_url(self, text: str) -> ParsedMediaUrl | None:
        parsed = _split_safe_https_url(text)
        if parsed is None:
            return None
        host = (parsed.hostname or "").rstrip(".").lower()
        if host not in INSTAGRAM_HOSTS:
            return None
        if match := PUBLICATION_PATH_RE.fullmatch(parsed.path):
            media_id = match.group("media_id")
            kind = "reel" if match.group("kind") in {"reel", "reels"} else "p"
            return ParsedMediaUrl(
                source=self.source,
                normalized_url=f"https://www.instagram.com/{kind}/{media_id}/",
                media_id=media_id,
            )
        if match := SHARE_PATH_RE.fullmatch(parsed.path):
            share_id = match.group("share_id")
            return ParsedMediaUrl(
                source=self.source,
                normalized_url=urlunsplit(
                    ("https", "www.instagram.com", f"/share/{share_id}/", "", "")
                ),
                media_id=None,
            )
        return None

    def parse_metadata(
        self,
        raw: dict[str, Any],
        parsed: ParsedMediaUrl,
        max_upload_bytes: int,
    ) -> MediaMetadata:
        chosen = best_video_format(list(raw.get("formats") or []))
        if chosen is None:
            raise MetadataError(
                "Instagram не вернул поддерживаемое видео",
                kind=MetadataErrorKind.UNSUPPORTED_MEDIA,
            )
        try:
            media_id = str(raw["id"])
            duration = int(raw["duration"]) if raw.get("duration") else None
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError(
                "Некорректные метаданные Instagram",
                kind=MetadataErrorKind.UNAVAILABLE,
            ) from exc
        estimated_size = format_size(chosen, duration)
        if estimated_size is not None and estimated_size > max_upload_bytes:
            raise MetadataError(
                "Видео Instagram превышает лимит размера",
                kind=MetadataErrorKind.TOO_LARGE,
            )
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title=str(raw.get("title") or "Instagram publication")[:512],
            duration=duration,
            thumbnail=str(raw["thumbnail"]) if raw.get("thumbnail") else None,
            media_kind=MediaKind.VIDEO,
            options=(
                DownloadOption(
                    key=self.automatic_option,
                    label="Скачать публикацию",
                    media_kind=MediaKind.VIDEO,
                    height=int(chosen["height"]) if chosen.get("height") else None,
                    estimated_size=estimated_size,
                    codec=str(chosen.get("vcodec") or "") or None,
                    container=str(chosen.get("ext") or "") or None,
                ),
            ),
        )

    def download_policy(self, option_key: str) -> DownloadPolicy:
        if option_key != self.automatic_option:
            raise ValueError("Неизвестный вариант Instagram")
        return DownloadPolicy(format_selector="best[ext=mp4]/best")
