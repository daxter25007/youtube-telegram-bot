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

TIKTOK_CANONICAL_HOSTS = frozenset({"tiktok.com", "www.tiktok.com", "m.tiktok.com"})
TIKTOK_SHORT_HOSTS = frozenset({"vm.tiktok.com", "vt.tiktok.com"})
CANONICAL_PATH_RE = re.compile(
    r"^/@(?P<account>[A-Za-z0-9._-]{1,64})/video/(?P<media_id>[0-9]{6,32})/?$"
)
SHORT_PATH_RE = re.compile(r"^/[A-Za-z0-9_-]{4,128}/?$")


class TikTokAdapter:
    source = MediaSource.TIKTOK
    automatic_option = "best_video"

    def initial_metadata(self, parsed: ParsedMediaUrl) -> MediaMetadata:
        media_id = parsed.media_id
        if media_id is None:
            digest = hashlib.sha256(parsed.normalized_url.encode("utf-8")).hexdigest()[:32]
            media_id = f"url_{digest}"
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title="TikTok video",
            duration=None,
            thumbnail=None,
            media_kind=MediaKind.VIDEO,
            options=(
                DownloadOption(
                    key=self.automatic_option,
                    label="Лучшее качество",
                    media_kind=MediaKind.VIDEO,
                ),
            ),
        )

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

    def parse_metadata(
        self,
        raw: dict[str, Any],
        parsed: ParsedMediaUrl,
        max_upload_bytes: int,
    ) -> MediaMetadata:
        chosen = best_video_format(list(raw.get("formats") or []))
        if chosen is None:
            raise MetadataError(
                "Фотопубликации TikTok пока не поддерживаются",
                kind=MetadataErrorKind.UNSUPPORTED_MEDIA,
            )
        try:
            media_id = str(raw["id"])
            duration = int(raw["duration"]) if raw.get("duration") else None
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError(
                "Некорректные метаданные TikTok", kind=MetadataErrorKind.UNAVAILABLE
            ) from exc
        estimated_size = format_size(chosen, duration)
        if estimated_size is not None and estimated_size > max_upload_bytes:
            raise MetadataError(
                "Видео TikTok превышает лимит размера", kind=MetadataErrorKind.TOO_LARGE
            )
        height = int(chosen["height"]) if chosen.get("height") else None
        option = DownloadOption(
            key="best_video",
            label="Лучшее качество",
            media_kind=MediaKind.VIDEO,
            height=height,
            estimated_size=estimated_size,
            codec=str(chosen.get("vcodec") or "") or None,
            container=str(chosen.get("ext") or "") or None,
        )
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title=str(raw.get("title") or "TikTok video")[:512],
            duration=duration,
            thumbnail=str(raw["thumbnail"]) if raw.get("thumbnail") else None,
            media_kind=MediaKind.VIDEO,
            options=(option,),
        )

    def download_policy(self, option_key: str) -> DownloadPolicy:
        if option_key != self.automatic_option:
            raise ValueError("Неизвестный вариант TikTok")
        return DownloadPolicy(format_selector="best[ext=mp4]/best")
