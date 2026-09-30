from __future__ import annotations

import asyncio
import contextlib
import ipaddress
import re
import socket
from collections.abc import Mapping
from pathlib import Path
from typing import Any, Protocol
from urllib.parse import urljoin

from aiohttp import ClientError, ClientSession, ClientTimeout, TCPConnector
from aiohttp.abc import AbstractResolver

from app.services.cobalt.client import CobaltClient, CobaltClientError, CobaltErrorKind
from app.services.cobalt.models import CobaltMode, CobaltPreparedItem
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
    MediaPartFailure,
    PreparedMedia,
)
from app.services.media.downloader import ProgressCallback
from app.services.media.models import MediaArtifact, MediaKind, MediaPackage, MediaPart
from app.services.media.probe import probe_media

CHUNK_SIZE = 1024 * 1024
MAX_REDIRECTS = 5
SAFE_SUFFIX_RE = re.compile(r"^\.[a-z0-9]{1,8}$")


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
                "family": socket.AF_INET6
                if ipaddress.ip_address(address).version == 6
                else socket.AF_INET,
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
        session = ClientSession(connector=TCPConnector(resolver=_PinnedResolver(validated)))
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

    async def prepare(self, request: MediaDownloadRequest) -> PreparedMedia:
        try:
            visual = await self.client.prepare(request.url, mode=CobaltMode.AUTO)
            items = list(visual.items)
            if not any(item.media_kind is MediaKind.AUDIO for item in items):
                audio = await self.client.prepare(
                    request.url,
                    mode=CobaltMode.AUDIO,
                    tiktok_full_audio=True,
                )
                items.extend(audio.items)
        except CobaltClientError as exc:
            kind = {
                CobaltErrorKind.UNSUPPORTED: BackendErrorKind.UNSUPPORTED,
                CobaltErrorKind.SECURITY: BackendErrorKind.SECURITY,
            }.get(exc.kind, BackendErrorKind.RETRYABLE)
            raise BackendError("Cobalt не подготовил публикацию", kind=kind) from exc
        normalized = tuple(
            CobaltPreparedItem(
                part_key=item.part_key,
                media_kind=item.media_kind,
                position=position,
                url=item.url,
                filename=item.filename,
                response_kind=item.response_kind,
                internal_origin=item.internal_origin,
            )
            for position, item in enumerate(items)
        )
        parts = tuple(
            MediaPart(
                key=item.part_key,
                media_kind=item.media_kind,
                position=item.position,
                filename=item.filename,
            )
            for item in normalized
        )
        return PreparedMedia(request=request, parts=parts, backend_payload=normalized)

    async def download_prepared(
        self,
        prepared: PreparedMedia,
        include_keys: frozenset[str],
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaPackage:
        payload = prepared.backend_payload
        if not isinstance(payload, tuple) or not all(
            isinstance(item, CobaltPreparedItem) for item in payload
        ):
            raise BackendError("Некорректная подготовка Cobalt", kind=BackendErrorKind.RETRYABLE)
        artifacts: list[MediaArtifact] = []
        failures: list[MediaPartFailure] = []
        for item in payload:
            if item.part_key not in include_keys:
                continue
            try:
                artifacts.append(await self._download_item(item, job_dir, progress))
            except asyncio.CancelledError:
                raise
            except BackendError as exc:
                if exc.kind in {BackendErrorKind.SECURITY, BackendErrorKind.FILESYSTEM}:
                    raise
                failures.append(
                    MediaPartFailure(
                        part_key=item.part_key,
                        media_kind=item.media_kind,
                        error_kind=exc.kind,
                    )
                )
        if (
            not artifacts
            and failures
            and all(failure.error_kind is BackendErrorKind.RETRYABLE for failure in failures)
        ):
            raise BackendError("Cobalt не скачал ни одной части", kind=BackendErrorKind.RETRYABLE)
        return MediaPackage(artifacts=tuple(artifacts), failures=tuple(failures))

    async def _download_item(
        self,
        item: CobaltPreparedItem,
        job_dir: Path,
        progress: ProgressCallback,
    ) -> MediaArtifact:
        suffix = Path(item.filename).suffix.lower()
        if not SAFE_SUFFIX_RE.fullmatch(suffix):
            suffix = {
                MediaKind.VIDEO: ".mp4",
                MediaKind.PHOTO: ".jpg",
                MediaKind.AUDIO: ".bin",
            }[item.media_kind]
        part_path = job_dir / f"{item.part_key}.part"
        final_path = job_dir / f"{item.part_key}{suffix}"
        response: StreamResponse | None = None
        file_handle: Any | None = None
        completed = False
        try:
            try:
                validated = await self.validator.validate(
                    item.url,
                    allowed_internal_origin=item.internal_origin,
                )
            except UnsafeRemoteUrlError as exc:
                raise BackendError(
                    "Cobalt вернул небезопасный URL", kind=BackendErrorKind.SECURITY
                ) from exc
            except OSError as exc:
                raise BackendError(
                    "Не удалось проверить media URL Cobalt", kind=BackendErrorKind.RETRYABLE
                ) from exc
            for redirect_count in range(MAX_REDIRECTS + 1):
                try:
                    response = await self.opener.open(validated)
                except (ClientError, OSError, TimeoutError) as exc:
                    raise BackendError(
                        "Cobalt media endpoint недоступен", kind=BackendErrorKind.RETRYABLE
                    ) from exc
                if response.status not in {301, 302, 303, 307, 308}:
                    break
                location = response.headers.get("Location")
                await response.close()
                response = None
                if not location or redirect_count == MAX_REDIRECTS:
                    raise BackendError(
                        "Некорректный Cobalt redirect", kind=BackendErrorKind.RETRYABLE
                    )
                try:
                    validated = await self.validator.validate(
                        urljoin(validated.url, location),
                        allowed_internal_origin=item.internal_origin,
                    )
                except UnsafeRemoteUrlError as exc:
                    raise BackendError(
                        "Cobalt redirect небезопасен", kind=BackendErrorKind.SECURITY
                    ) from exc
                except OSError as exc:
                    raise BackendError(
                        "Не удалось проверить Cobalt redirect", kind=BackendErrorKind.RETRYABLE
                    ) from exc
            if response is None or response.status != 200:
                raise BackendError(
                    "Cobalt media stream недоступен", kind=BackendErrorKind.RETRYABLE
                )
            content_type = response.headers.get("Content-Type", "").partition(";")[0].lower()
            if content_type.startswith("text/") or content_type == "application/json":
                raise BackendError("Cobalt вернул не медиа", kind=BackendErrorKind.RETRYABLE)
            declared_size = self._content_length(response.headers)
            if declared_size is not None and declared_size > self.max_upload_bytes:
                raise BackendError("Cobalt media превышает лимит", kind=BackendErrorKind.TOO_LARGE)
            try:
                file_handle = await asyncio.to_thread(part_path.open, "wb")
            except OSError as exc:
                raise BackendError(
                    "Не удалось создать временный файл", kind=BackendErrorKind.FILESYSTEM
                ) from exc
            written = 0
            try:
                async for chunk in response.content.iter_chunked(CHUNK_SIZE):
                    written += len(chunk)
                    if written > self.max_upload_bytes:
                        raise BackendError(
                            "Cobalt media превышает лимит", kind=BackendErrorKind.TOO_LARGE
                        )
                    await asyncio.to_thread(file_handle.write, chunk)
                    percent = min(100.0, written * 100 / declared_size) if declared_size else None
                    await progress("downloading", percent)
            except BackendError:
                raise
            except (ClientError, OSError, TimeoutError) as exc:
                raise BackendError(
                    "Cobalt stream прерван", kind=BackendErrorKind.RETRYABLE
                ) from exc
            await asyncio.to_thread(file_handle.close)
            file_handle = None
            await asyncio.to_thread(part_path.replace, final_path)
            width = height = duration = None
            if item.media_kind is MediaKind.VIDEO:
                probe = await probe_media(final_path)
                if probe.width is None or probe.height is None:
                    raise BackendError(
                        "Cobalt payload не содержит видеопоток", kind=BackendErrorKind.RETRYABLE
                    )
                width, height, duration = probe.width, probe.height, probe.duration
            completed = True
            return MediaArtifact(
                path=final_path,
                media_kind=item.media_kind,
                part_key=item.part_key,
                width=width,
                height=height,
                duration=duration,
                mime_type=content_type or None,
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
                "Некорректный Content-Length", kind=BackendErrorKind.RETRYABLE
            ) from exc
        if value < 0:
            raise BackendError("Отрицательный Content-Length", kind=BackendErrorKind.RETRYABLE)
        return value
