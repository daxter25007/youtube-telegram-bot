from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from app.services.media.downloader import ProgressCallback
from app.services.media.models import DownloadPolicy, MediaPackage, MediaSource


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
class BackendResult:
    package: MediaPackage
    backend: DownloadBackend


class MediaBackend(Protocol):
    name: DownloadBackend

    async def download(
        self,
        request: MediaDownloadRequest,
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage: ...
