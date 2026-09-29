from __future__ import annotations

from typing import Any, Protocol

from app.services.media.models import (
    DownloadPolicy,
    MediaMetadata,
    MediaSource,
    ParsedMediaUrl,
)


class MediaAdapter(Protocol):
    source: MediaSource
    automatic_option: str | None

    def parse_url(self, text: str) -> ParsedMediaUrl | None: ...

    def initial_metadata(self, parsed: ParsedMediaUrl) -> MediaMetadata | None: ...

    def parse_metadata(
        self,
        raw: dict[str, Any],
        parsed: ParsedMediaUrl,
        max_upload_bytes: int,
    ) -> MediaMetadata: ...

    def download_policy(self, option_key: str) -> DownloadPolicy: ...
