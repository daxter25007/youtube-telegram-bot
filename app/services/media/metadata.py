from __future__ import annotations

import asyncio
import json
import sys
from typing import Any

from app.services.media.errors import MetadataError, classify_metadata_error
from app.services.media.models import MediaMetadata, ParsedMediaUrl
from app.services.media.registry import MediaRegistry
from app.services.youtube.process import stop_process, subprocess_options


class YtDlpMetadataService:
    def __init__(
        self,
        *,
        registry: MediaRegistry,
        timeout_seconds: int,
        max_upload_bytes: int,
        cookies_file: str | None = None,
    ) -> None:
        self.registry = registry
        self.timeout_seconds = timeout_seconds
        self.max_upload_bytes = max_upload_bytes
        self.cookies_file = cookies_file

    async def fetch(self, parsed: ParsedMediaUrl) -> MediaMetadata:
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
        command.extend(["--", parsed.normalized_url])
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
            message = stderr.decode("utf-8", errors="replace")[-2_000:]
            raise MetadataError(
                message or "yt-dlp не получил метаданные",
                kind=classify_metadata_error(parsed.source, message),
            )
        try:
            raw: dict[str, Any] = json.loads(stdout)
        except (TypeError, json.JSONDecodeError) as exc:
            raise MetadataError(
                "yt-dlp вернул некорректные метаданные",
                kind=classify_metadata_error(parsed.source, "invalid metadata"),
            ) from exc
        adapter = self.registry.adapter_for(parsed.source)
        return adapter.parse_metadata(raw, parsed, self.max_upload_bytes)
