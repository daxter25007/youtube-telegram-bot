from __future__ import annotations

from enum import StrEnum
from typing import Any, Protocol

from aiohttp import ClientError, ClientTimeout

from app.services.cobalt.models import CobaltMediaResponse, CobaltResponseKind
from app.services.cobalt.security import SafeRemoteUrlValidator, UnsafeRemoteUrlError


class CobaltErrorKind(StrEnum):
    RETRYABLE = "retryable"
    UNSUPPORTED = "unsupported"
    SECURITY = "security"


class CobaltClientError(RuntimeError):
    def __init__(self, message: str, *, kind: CobaltErrorKind) -> None:
        super().__init__(message)
        self.kind = kind


class HttpSession(Protocol):
    async def post(self, url: str, **kwargs: Any) -> Any: ...

    async def get(self, url: str, **kwargs: Any) -> Any: ...


class CobaltClient:
    def __init__(
        self,
        *,
        base_url: str,
        request_timeout_seconds: int,
        session: HttpSession,
        validator: SafeRemoteUrlValidator,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self.request_timeout_seconds = request_timeout_seconds
        self.session = session
        self.validator = validator

    async def prepare(self, source_url: str) -> CobaltMediaResponse:
        try:
            response = await self.session.post(
                self.base_url,
                json={
                    "url": source_url,
                    "downloadMode": "auto",
                    "videoQuality": "max",
                    "allowH265": False,
                    "disableMetadata": False,
                    "alwaysProxy": True,
                    "localProcessing": "disabled",
                },
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=ClientTimeout(total=self.request_timeout_seconds),
            )
        except (ClientError, OSError, TimeoutError) as exc:
            raise CobaltClientError(
                "Cobalt API недоступен", kind=CobaltErrorKind.RETRYABLE
            ) from exc
        try:
            if response.status < 200 or response.status >= 300:
                raise CobaltClientError(
                    "Cobalt API вернул ошибку", kind=CobaltErrorKind.RETRYABLE
                )
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError, ClientError) as exc:
                raise CobaltClientError(
                    "Cobalt API вернул некорректный JSON",
                    kind=CobaltErrorKind.RETRYABLE,
                ) from exc
            return await self._parse_payload(payload)
        finally:
            response.release()

    async def _parse_payload(self, payload: object) -> CobaltMediaResponse:
        if not isinstance(payload, dict):
            raise CobaltClientError(
                "Cobalt API вернул некорректную схему",
                kind=CobaltErrorKind.RETRYABLE,
            )
        status = payload.get("status")
        if status == "picker":
            picker = payload.get("picker")
            if not isinstance(picker, list):
                raise CobaltClientError(
                    "Cobalt picker имеет некорректную схему",
                    kind=CobaltErrorKind.RETRYABLE,
                )
            video = next(
                (
                    item
                    for item in picker
                    if isinstance(item, dict)
                    and item.get("type") == "video"
                    and isinstance(item.get("url"), str)
                ),
                None,
            )
            if video is None:
                raise CobaltClientError(
                    "Cobalt не нашёл видео в публикации",
                    kind=CobaltErrorKind.UNSUPPORTED,
                )
            url = str(video["url"])
            filename = str(video.get("filename") or "media.mp4")
            kind = CobaltResponseKind.PICKER
            internal_origin = None
        elif status in {"tunnel", "redirect"}:
            url_value = payload.get("url")
            if not isinstance(url_value, str) or not url_value:
                raise CobaltClientError(
                    "Cobalt response не содержит media URL",
                    kind=CobaltErrorKind.RETRYABLE,
                )
            url = url_value
            filename = str(payload.get("filename") or "media.mp4")
            kind = CobaltResponseKind(str(status))
            internal_origin = self.base_url if kind is CobaltResponseKind.TUNNEL else None
        elif status == "error":
            raise CobaltClientError("Cobalt не получил видео", kind=CobaltErrorKind.RETRYABLE)
        elif status == "local-processing":
            raise CobaltClientError(
                "Cobalt запросил неподдерживаемую локальную обработку",
                kind=CobaltErrorKind.RETRYABLE,
            )
        else:
            raise CobaltClientError(
                "Cobalt API вернул неизвестный статус",
                kind=CobaltErrorKind.RETRYABLE,
            )
        try:
            await self.validator.validate(url, allowed_internal_origin=internal_origin)
        except UnsafeRemoteUrlError as exc:
            raise CobaltClientError(
                "Cobalt API вернул небезопасный URL",
                kind=CobaltErrorKind.SECURITY,
            ) from exc
        return CobaltMediaResponse(kind=kind, url=url, filename=filename)

    async def healthcheck(self) -> bool:
        try:
            response = await self.session.get(
                self.base_url,
                timeout=ClientTimeout(total=min(self.request_timeout_seconds, 3)),
            )
        except (ClientError, OSError, TimeoutError):
            return False
        try:
            return 200 <= response.status < 300
        finally:
            response.release()
