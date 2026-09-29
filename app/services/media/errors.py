from __future__ import annotations

from enum import StrEnum

from app.services.media.models import MediaSource


class MetadataErrorKind(StrEnum):
    UNAVAILABLE = "unavailable"
    RESTRICTED = "restricted"
    AUTHENTICATION = "authentication"
    UNSUPPORTED_MEDIA = "unsupported_media"
    EXTRACTOR_CHANGED = "extractor_changed"
    TOO_LARGE = "too_large"


class MetadataError(RuntimeError):
    def __init__(self, message: str, *, kind: MetadataErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


def classify_metadata_error(source: MediaSource, message: str) -> MetadataErrorKind:
    lowered = message.lower()
    if any(
        marker in lowered
        for marker in ("age-restricted", "not available in your country", "geo-restricted")
    ):
        return MetadataErrorKind.RESTRICTED
    if any(marker in lowered for marker in ("requires login", "login required", "--cookies")):
        return MetadataErrorKind.AUTHENTICATION
    if source is MediaSource.TIKTOK and any(
        marker in lowered
        for marker in (
            "unable to extract webpage video data",
            "unexpected response from webpage request",
            "unable to extract universal data",
        )
    ):
        return MetadataErrorKind.EXTRACTOR_CHANGED
    return MetadataErrorKind.UNAVAILABLE
