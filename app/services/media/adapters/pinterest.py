from __future__ import annotations

import hashlib
import re
from typing import Any

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

PINTEREST_HOST_RE = re.compile(r"^(?:(?:www|[a-z]{2}(?:-[a-z]{2})?)\.)?pinterest\.com$")
PIN_PATH_RE = re.compile(r"^/pin/(?P<media_id>[A-Za-z0-9_-]{1,128})(?:/[A-Za-z0-9_-]{1,128})?/?$")
SHORT_PATH_RE = re.compile(r"^/(?P<short_id>[A-Za-z0-9_-]{1,32})/?$")


class PinterestAdapter:
    source = MediaSource.PINTEREST
    automatic_option = "pinterest_bundle"

    def initial_metadata(self, parsed: ParsedMediaUrl) -> MediaMetadata:
        media_id = parsed.media_id
        if media_id is None:
            digest = hashlib.sha256(parsed.normalized_url.encode("utf-8")).hexdigest()[:32]
            media_id = f"url_{digest}"
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title="Pinterest Pin",
            duration=None,
            thumbnail=None,
            media_kind=MediaKind.VIDEO,
            options=(
                DownloadOption(
                    key=self.automatic_option,
                    label="Скачать Pin",
                    media_kind=MediaKind.VIDEO,
                ),
            ),
        )

    def parse_url(self, text: str) -> ParsedMediaUrl | None:
        parsed = _split_safe_https_url(text)
        if parsed is None:
            return None
        host = (parsed.hostname or "").rstrip(".").lower()
        if host == "pin.it":
            match = SHORT_PATH_RE.fullmatch(parsed.path)
            if match is None:
                return None
            return ParsedMediaUrl(
                source=self.source,
                normalized_url=f"https://pin.it/{match.group('short_id')}",
                media_id=None,
            )
        if PINTEREST_HOST_RE.fullmatch(host) is None:
            return None
        match = PIN_PATH_RE.fullmatch(parsed.path)
        if match is None:
            return None
        media_id = match.group("media_id")
        return ParsedMediaUrl(
            source=self.source,
            normalized_url=f"https://www.pinterest.com/pin/{media_id}/",
            media_id=media_id,
        )

    def parse_metadata(
        self,
        raw: dict[str, Any],
        parsed: ParsedMediaUrl,
        max_upload_bytes: int,
    ) -> MediaMetadata:
        chosen = best_video_format(list(raw.get("formats") or []))
        if chosen is None:
            raise MetadataError(
                "Pinterest не вернул поддерживаемое видео",
                kind=MetadataErrorKind.UNSUPPORTED_MEDIA,
            )
        try:
            media_id = str(raw["id"])
            duration = int(raw["duration"]) if raw.get("duration") else None
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError(
                "Некорректные метаданные Pinterest",
                kind=MetadataErrorKind.UNAVAILABLE,
            ) from exc
        estimated_size = format_size(chosen, duration)
        if estimated_size is not None and estimated_size > max_upload_bytes:
            raise MetadataError(
                "Видео Pinterest превышает лимит размера",
                kind=MetadataErrorKind.TOO_LARGE,
            )
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title=str(raw.get("title") or "Pinterest Pin")[:512],
            duration=duration,
            thumbnail=str(raw["thumbnail"]) if raw.get("thumbnail") else None,
            media_kind=MediaKind.VIDEO,
            options=(
                DownloadOption(
                    key=self.automatic_option,
                    label="Скачать Pin",
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
            raise ValueError("Неизвестный вариант Pinterest")
        return DownloadPolicy(format_selector="best[ext=mp4]/best")
