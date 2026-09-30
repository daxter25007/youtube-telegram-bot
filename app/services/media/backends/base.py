from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.services.media.downloader import ProgressCallback
from app.services.media.models import (
    DownloadPolicy,
    MediaKind,
    MediaPackage,
    MediaPart,
    MediaSource,
)


class DownloadBackend(StrEnum):
    YTDLP = "ytdlp"
    COBALT = "cobalt"


class BackendErrorKind(StrEnum):
    RETRYABLE = "retryable"
    TOO_LARGE = "too_large"
    CANCELLED = "cancelled"
    FILESYSTEM = "filesystem"
    UNSUPPORTED = "unsupported"
    SECURITY = "security"


class BackendError(RuntimeError):
    def __init__(self, message: str, *, kind: BackendErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True, slots=True)
class MediaDownloadRequest:
    source: MediaSource
    url: str
    option_key: str
    policy: DownloadPolicy


@dataclass(frozen=True, slots=True)
class PreparedMedia:
    request: MediaDownloadRequest
    parts: tuple[MediaPart, ...]
    backend_payload: object | None = None


@dataclass(frozen=True, slots=True)
class MediaPartFailure:
    part_key: str
    media_kind: MediaKind
    error_kind: BackendErrorKind


@dataclass(frozen=True, slots=True)
class BackendResult:
    package: MediaPackage
    backend: DownloadBackend


class MediaBackend(Protocol):
    name: DownloadBackend

    async def prepare(self, request: MediaDownloadRequest) -> PreparedMedia: ...

    async def download_prepared(
        self,
        prepared: PreparedMedia,
        include_keys: frozenset[str],
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage: ...
