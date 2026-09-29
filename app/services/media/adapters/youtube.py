from __future__ import annotations

import re
from typing import Any
from urllib.parse import parse_qs, urlsplit, urlunsplit

from app.services.media.errors import MetadataError, MetadataErrorKind
from app.services.media.models import (
    DownloadOption,
    DownloadPolicy,
    MediaKind,
    MediaMetadata,
    MediaSource,
    ParsedMediaUrl,
)
from app.services.youtube.formats import build_format_selector, select_quality_options

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

    def parse_metadata(
        self,
        raw: dict[str, Any],
        parsed: ParsedMediaUrl,
        max_upload_bytes: int,
    ) -> MediaMetadata:
        try:
            media_id = str(raw["id"])
            duration = int(raw["duration"]) if raw.get("duration") else None
            qualities = select_quality_options(
                list(raw.get("formats") or []), duration, max_upload_bytes
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise MetadataError(
                "Некорректные метаданные YouTube", kind=MetadataErrorKind.UNAVAILABLE
            ) from exc
        if not qualities:
            raise MetadataError("Нет доступных форматов видео", kind=MetadataErrorKind.UNAVAILABLE)
        options = tuple(
            DownloadOption(
                key=f"video_{quality.height}",
                label=f"{quality.height}p",
                media_kind=MediaKind.VIDEO,
                height=quality.height,
                estimated_size=quality.estimated_size,
                fps=quality.fps,
                codec=quality.codec,
                container=quality.container,
            )
            for quality in qualities
        )
        return MediaMetadata(
            source=self.source,
            media_id=media_id,
            source_url=parsed.normalized_url,
            title=str(raw.get("title") or "YouTube video")[:512],
            duration=duration,
            thumbnail=str(raw["thumbnail"]) if raw.get("thumbnail") else None,
            media_kind=MediaKind.VIDEO,
            options=options,
        )

    def download_policy(self, option_key: str) -> DownloadPolicy:
        prefix = "video_"
        if not option_key.startswith(prefix):
            raise ValueError("Неизвестный вариант YouTube")
        try:
            height = int(option_key.removeprefix(prefix))
        except ValueError as exc:
            raise ValueError("Некорректное качество YouTube") from exc
        return DownloadPolicy(format_selector=build_format_selector(height))


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
