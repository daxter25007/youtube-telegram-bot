from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector
from aiohttp.abc import AbstractResolver

from app.services.cobalt.client import CobaltClient, CobaltClientError, CobaltErrorKind
from app.services.cobalt.models import CobaltResponseKind
from app.services.cobalt.security import (
    SafeRemoteUrlValidator,
    UnsafeRemoteUrlError,
    ValidatedRemoteUrl,
)
from app.services.media.backends.base import (
    BackendError,
    BackendErrorKind,
    DownloadBackend,
    MediaDownloadRequest,
)
from app.services.media.downloader import ProgressCallback
from app.services.media.models import MediaArtifact, MediaKind, MediaPackage
from app.services.media.probe import probe_media

CHUNK_SIZE = 1024 * 1024
MAX_REDIRECTS = 5


class StreamContent(Protocol):
    def iter_chunked(self, size: int) -> Any: ...


class StreamResponse(Protocol):
    status: int
    headers: Mapping[str, str]
    content: StreamContent

    async def close(self) -> None: ...


class ResponseOpener(Protocol):
    async def open(self, validated: ValidatedRemoteUrl) -> StreamResponse: ...


class _PinnedResolver(AbstractResolver):
    def __init__(self, validated: ValidatedRemoteUrl) -> None:
        self.validated = validated

    async def resolve(
        self,
        host: str,
        port: int = 0,
        family: socket.AddressFamily = socket.AF_INET,
    ) -> list[dict[str, Any]]:
        if host.rstrip(".").lower() != self.validated.hostname:
            return []
        return [
            {
                "hostname": host,
                "host": address,
                "port": port,
                "family": (
                    socket.AF_INET6
                    if ipaddress.ip_address(address).version == 6
                    else socket.AF_INET
                ),
                "proto": 0,
                "flags": socket.AI_NUMERICHOST,
            }
            for address in self.validated.addresses
        ]

    async def close(self) -> None:
        return None


class _ManagedAiohttpResponse:
    def __init__(self, response: Any, owned_session: ClientSession | None) -> None:
        self._response = response
        self._owned_session = owned_session
        self.status = response.status
        self.headers = response.headers
        self.content = response.content

    async def close(self) -> None:
        self._response.release()
        if self._owned_session is not None:
            await self._owned_session.close()


class AiohttpResponseOpener:
    def __init__(self, internal_session: ClientSession, timeout_seconds: int) -> None:
        self.internal_session = internal_session
        self.timeout = ClientTimeout(total=timeout_seconds)

    async def open(self, validated: ValidatedRemoteUrl) -> StreamResponse:
        if validated.is_internal:
            response = await self.internal_session.get(
                validated.url,
                allow_redirects=False,
                timeout=self.timeout,
            )
            return _ManagedAiohttpResponse(response, None)
        session = ClientSession(
            connector=TCPConnector(resolver=_PinnedResolver(validated)),
        )
        try:
            response = await session.get(
                validated.url,
                allow_redirects=False,
                timeout=self.timeout,
            )
        except BaseException:
            await session.close()
            raise
        return _ManagedAiohttpResponse(response, session)


class CobaltBackend:
    name = DownloadBackend.COBALT

    def __init__(
        self,
        *,
        client: CobaltClient,
        validator: SafeRemoteUrlValidator,
        opener: ResponseOpener,
        max_upload_bytes: int,
    ) -> None:
        self.client = client
        self.validator = validator
        self.opener = opener
        self.max_upload_bytes = max_upload_bytes

    async def download(
        self,
        request: MediaDownloadRequest,
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage:
        part_path = job_dir / "media.part"
        final_path = job_dir / "media.mp4"
        completed = False
        response: StreamResponse | None = None
        file_handle: Any | None = None
        try:
            try:
                prepared = await self.client.prepare(request.url)
                allowed_origin = (
                    self.client.base_url
                    if prepared.kind is CobaltResponseKind.TUNNEL
                    else None
                )
                validated = await self.validator.validate(
                    prepared.url,
                    allowed_internal_origin=allowed_origin,
                )
            except CobaltClientError as exc:
                kind = (
                    BackendErrorKind.UNSUPPORTED
                    if exc.kind is CobaltErrorKind.UNSUPPORTED
                    else BackendErrorKind.RETRYABLE
                )
                raise BackendError("Cobalt не подготовил видео", kind=kind) from exc
            except UnsafeRemoteUrlError as exc:
                raise BackendError(
                    "Cobalt вернул небезопасный URL",
                    kind=BackendErrorKind.RETRYABLE,
                ) from exc

            for redirect_count in range(MAX_REDIRECTS + 1):
                response = await self.opener.open(validated)
                if response.status not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("Location")
                await response.close()
                response = None
                if not location or redirect_count == MAX_REDIRECTS:
                    raise BackendError(
                        "Cobalt redirect не содержит безопасной цели",
                        kind=BackendErrorKind.RETRYABLE,
                    )
                redirect_url = urljoin(validated.url, location)
                try:
                    validated = await self.validator.validate(
                        redirect_url,
                        allowed_internal_origin=allowed_origin,
                    )
                except UnsafeRemoteUrlError as exc:
                    raise BackendError(
                        "Cobalt redirect небезопасен",
                        kind=BackendErrorKind.RETRYABLE,
                    ) from exc
            if response is None or response.status != 200:
                raise BackendError(
                    "Cobalt media stream недоступен",
                    kind=BackendErrorKind.RETRYABLE,
                )
            declared_size = self._content_length(response.headers)
            if declared_size is not None and declared_size > self.max_upload_bytes:
                raise BackendError(
                    "Cobalt media превышает лимит размера",
                    kind=BackendErrorKind.TOO_LARGE,
                )
            try:
                file_handle = await asyncio.to_thread(part_path.open, "wb")
            except OSError as exc:
                raise BackendError(
                    "Не удалось создать временный файл",
                    kind=BackendErrorKind.FILESYSTEM,
                ) from exc
            written = 0
            try:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    written += len(chunk)
                    if written > self.max_upload_bytes:
                        raise BackendError(
                            "Cobalt media превышает лимит размера",
                            kind=BackendErrorKind.TOO_LARGE,
                        )
                    try:
                        await asyncio.to_thread(file_handle.write, chunk)
                    except OSError as exc:
                        raise BackendError(
                            "Не удалось записать временный файл",
                            kind=BackendErrorKind.FILESYSTEM,
                        ) from exc
                    percent = (
                        min(100.0, written * 100 / declared_size)
                        if declared_size
                        else None
                    )
                    await progress("downloading", percent)
            except asyncio.CancelledError:
                raise
            except BackendError:
                raise
            except (ClientError, OSError, TimeoutError) as exc:
                raise BackendError(
                    "Cobalt stream прерван", kind=BackendErrorKind.RETRYABLE
                ) from exc
            await asyncio.to_thread(file_handle.close)
            file_handle = None
            try:
                await asyncio.to_thread(part_path.replace, final_path)
            except OSError as exc:
                raise BackendError(
                    "Не удалось завершить временный файл",
                    kind=BackendErrorKind.FILESYSTEM,
                ) from exc
            probe = await probe_media(final_path)
            completed = True
            return MediaPackage(
                artifacts=(
                    MediaArtifact(
                        path=final_path,
                        media_kind=MediaKind.VIDEO,
                        width=probe.width,
                        height=probe.height,
                        duration=probe.duration,
                        mime_type="video/mp4",
                    ),
                )
            )
        finally:
            if file_handle is not None:
                await asyncio.to_thread(file_handle.close)
            if response is not None:
                await response.close()
            if not completed:
                for path in (part_path, final_path):
                    with contextlib.suppress(OSError):
                        await asyncio.to_thread(path.unlink, missing_ok=True)

    @staticmethod
    def _content_length(headers: Mapping[str, str]) -> int | None:
        raw = headers.get("Content-Length")
        if raw is None:
            return None
        try:
            value = int(raw)
        except ValueError as exc:
            raise BackendError(
                "Cobalt вернул некорректный Content-Length",
                kind=BackendErrorKind.RETRYABLE,
            ) from exc
        if value < 0:
            raise BackendError(
                "Cobalt вернул отрицательный Content-Length",
                kind=BackendErrorKind.RETRYABLE,
            )
        return value
