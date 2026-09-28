from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any

from app.services.youtube.formats import QualityOption, select_quality_options
from app.services.youtube.process import stop_process, subprocess_options


class MetadataError(RuntimeError):
    def __init__(self, message: str, *, kind: str = "unavailable") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class VideoMetadata:
    video_id: str
    title: str
    duration: int | None
    thumbnail: str | None
    qualities: tuple[QualityOption, ...]

    def as_dict(self) -> dict[str, object]:
        return {
            "video_id": self.video_id,
            "title": self.title,
            "duration": self.duration,
            "thumbnail": self.thumbnail,
            "qualities": [quality.as_dict() for quality in self.qualities],
        }


class MetadataService:
    def __init__(
        self,
        *,
        timeout_seconds: int,
        max_upload_bytes: int,
        cookies_file: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.max_upload_bytes = max_upload_bytes
        self.cookies_file = cookies_file

    async def fetch(self, url: str) -> VideoMetadata:
        command = [
            sys.executable,
            "-m",
            "yt_dlp",
            "--dump-single-json",
            "--skip-download",
            "--no-warnings",
            "--no-playlist",
            "--socket-timeout",
            "30",
            "--js-runtimes",
            "node",
        ]
        if self.cookies_file:
            command.extend(["--cookies", self.cookies_file])
        command.extend(["--", url])
        process = await asyncio.create_subprocess_exec(
            *command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            **subprocess_options(),
        )
        try:
            async with asyncio.timeout(self.timeout_seconds):
                stdout, stderr = await process.communicate()
        except (TimeoutError, asyncio.CancelledError):
            await stop_process(process)
            raise
        if process.returncode != 0:
            message = stderr.decode("utf-8", errors="replace")[-1000:]
            lowered = message.lower()
            restricted_markers = (
                "age-restricted",
                "confirm your age",
                "not available in your country",
                "geo-restricted",
                "region",
            )
            kind = (
                "restricted"
                if any(marker in lowered for marker in restricted_markers)
                else "unavailable"
            )
            raise MetadataError(message or "yt-dlp metadata extraction failed", kind=kind)
        try:
            raw: dict[str, Any] = json.loads(stdout)
            video_id = str(raw["id"])
            title = str(raw.get("title") or "YouTube video")[:512]
            duration = int(raw["duration"]) if raw.get("duration") else None
            formats = raw.get("formats") or []
            qualities = select_quality_options(formats, duration, self.max_upload_bytes)
        except (KeyError, TypeError, ValueError, json.JSONDecodeError) as exc:
            raise MetadataError("Invalid metadata returned by yt-dlp") from exc
        if not qualities:
            raise MetadataError("No suitable video formats are available")
        return VideoMetadata(
            video_id=video_id,
            title=title,
            duration=duration,
            thumbnail=str(raw["thumbnail"]) if raw.get("thumbnail") else None,
            qualities=tuple(qualities),
        )
