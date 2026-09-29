from __future__ import annotations

import asyncio
import shutil
from collections.abc import Awaitable, Callable, Mapping, Sequence
from pathlib import Path

from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    BackendResult,
    DownloadBackend,
    MediaBackend,
    MediaDownloadRequest,
)
from app.services.media.downloader import ProgressCallback
from app.services.media.models import MediaSource

FallbackCallback = Callable[[DownloadBackend, DownloadBackend], Awaitable[None]]


class MediaBackendRouter:
    def __init__(self, routes: Mapping[MediaSource, Sequence[MediaBackend]]) -> None:
        self._routes = {source: tuple(backends) for source, backends in routes.items()}

    async def download(
        self,
        request: MediaDownloadRequest,
        job_dir: Path,
        progress: ProgressCallback,
        on_fallback: FallbackCallback,
    ) -> BackendResult:
        backends = self._routes.get(request.source, ())
        if not backends:
            raise BackendError(
                "Для источника не настроен backend",
                kind=BackendErrorKind.UNSUPPORTED,
            )
        for index, backend in enumerate(backends):
            try:
                package = await backend.download(request, job_dir, progress)
                return BackendResult(package=package, backend=backend.name)
            except BackendError as exc:
                has_fallback = index + 1 < len(backends)
                if exc.kind is not BackendErrorKind.RETRYABLE or not has_fallback:
                    raise
                following = backends[index + 1]
                await asyncio.to_thread(self._clear_job_contents, job_dir)
                await on_fallback(backend.name, following.name)
        raise BackendError(
            "Backend chain завершилась без результата", kind=BackendErrorKind.RETRYABLE
        )

    @staticmethod
    def _clear_job_contents(job_dir: Path) -> None:
        try:
            root = job_dir.resolve(strict=True)
            if not root.is_dir():
                raise OSError("job path is not a directory")
            for child in root.iterdir():
                if child.is_symlink() or child.is_file():
                    child.unlink()
                    continue
                resolved = child.resolve(strict=True)
                if resolved.parent != root:
                    raise OSError("nested path escaped job directory")
                shutil.rmtree(child)
        except OSError as exc:
            raise BackendError(
                "Не удалось очистить временные файлы backend-а",
                kind=BackendErrorKind.FILESYSTEM,
            ) from exc
