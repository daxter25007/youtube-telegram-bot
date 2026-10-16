from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlencode, urlunsplit

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

VK_HOSTS = frozenset(
    {
        "vk.com",
        "www.vk.com",
        "m.vk.com",
        "new.vk.com",
        "vk.ru",
        "www.vk.ru",
        "m.vk.ru",
        "vkvideo.ru",
        "www.vkvideo.ru",
        "m.vkvideo.ru",
        "vksport.vkvideo.ru",
    }
)
DIRECT_PATH_RE = re.compile(r"^/(?P<kind>video|clip)(?P<media_id>-?[0-9]+_[0-9]+)/?$")
SHARED_TARGET_RE = re.compile(r"^(?P<kind>video|clip)(?P<media_id>-?[0-9]+_[0-9]+)(?:/|$)")
LIST_ID_RE = re.compile(r"^(?:[0-9a-f]+|ln-[0-9A-Za-z]+)$")


class VKAdapter:
    source = MediaSource.VK
    automatic_option = "vk_video"

    def initial_metadata(self, parsed: ParsedMediaUrl) -> MediaMetadata:
        is_clip = "/clip" in parsed.normalized_url
        return MediaMetadata(
            source=self.source,
            media_id=parsed.media_id or "",
            source_url=parsed.normalized_url,
            title="VK Clip" if is_clip else "VK video",
            duration=None,
            thumbnail=None,
            media_kind=MediaKind.VIDEO,
            options=(
                DownloadOption(
                    key=self.automatic_option,
                    label="Скачать видео",
                    media_kind=MediaKind.VIDEO,
                ),
            ),
        )

    def parse_url(self, text: str) -> ParsedMediaUrl | None:
        parsed = _split_safe_https_url(text)
        if parsed is None:
            return None
        host = (parsed.hostname or "").rstrip(".").lower()
        if host not in VK_HOSTS:
            return None

        match = DIRECT_PATH_RE.fullmatch(parsed.path)
        if match is None:
            query = parse_qs(parsed.query, keep_blank_values=False)
            targets = query.get("z", ())
            match = next(
                (candidate for value in targets if (candidate := SHARED_TARGET_RE.match(value))),
                None,
            )
        if match is None:
            return None

        kind = match.group("kind")
        media_id = match.group("media_id")
        query_values = parse_qs(parsed.query, keep_blank_values=False)
        list_id = next(
            (value for value in query_values.get("list", ()) if LIST_ID_RE.fullmatch(value)),
            None,
        )
        normalized_query = urlencode({"list": list_id}) if list_id else ""
        return ParsedMediaUrl(
            source=self.source,
            normalized_url=urlunsplit(
                ("https", "vk.com", f"/{kind}{media_id}", normalized_query, "")
            ),
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
                "VK не вернул поддерживаемое видео",
                kind=MetadataErrorKind.UNSUPPORTED_MEDIA,
            )
        try:
            media_id = str(raw["id"])
            duration = int(raw["duration"]) if raw.get("duration") else None
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError(
                "Некорректные метаданные VK",
                kind=MetadataErrorKind.UNAVAILABLE,
            ) from exc
        estimated_size = format_size(chosen, duration)
        if estimated_size is not None and estimated_size > max_upload_bytes:
            raise MetadataError(
                "Видео VK превышает лимит размера",
                kind=MetadataErrorKind.TOO_LARGE,
            )
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title=str(raw.get("title") or "VK video")[:512],
            duration=duration,
            thumbnail=str(raw["thumbnail"]) if raw.get("thumbnail") else None,
            media_kind=MediaKind.VIDEO,
            options=(
                DownloadOption(
                    key=self.automatic_option,
                    label="Скачать видео",
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
            raise ValueError("Неизвестный вариант VK")
        return DownloadPolicy(format_selector="best[ext=mp4]/best")
