from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Any, Literal, Protocol

from aiohttp import ClientError, ClientTimeout

from app.services.cobalt.models import (
    CobaltMediaResponse,
    CobaltMode,
    CobaltPreparedItem,
    CobaltResponseKind,
)
from app.services.cobalt.security import SafeRemoteUrlValidator, UnsafeRemoteUrlError
from app.services.media.models import MediaKind


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

    async def prepare(
        self,
        source_url: str,
        *,
        mode: CobaltMode = CobaltMode.AUTO,
        tiktok_full_audio: bool = False,
        audio_format: Literal["best", "mp3"] = "best",
    ) -> CobaltMediaResponse:
        body: dict[str, object] = {
            "url": source_url,
            "downloadMode": mode.value,
            "videoQuality": "max",
            "allowH265": False,
            "disableMetadata": False,
            "alwaysProxy": True,
            "localProcessing": "disabled",
            "audioFormat": audio_format,
            "tiktokFullAudio": tiktok_full_audio,
        }
        try:
            response = await self.session.post(
                self.base_url,
                json=body,
                headers={"Accept": "application/json", "Content-Type": "application/json"},
                timeout=ClientTimeout(total=self.request_timeout_seconds),
            )
        except (ClientError, OSError, TimeoutError) as exc:
            raise CobaltClientError(
                "Cobalt API недоступен", kind=CobaltErrorKind.RETRYABLE
            ) from exc
        try:
            try:
                payload = await response.json(content_type=None)
            except (TypeError, ValueError, ClientError, OSError) as exc:
                raise CobaltClientError(
                    "Cobalt API вернул некорректный JSON",
                    kind=CobaltErrorKind.RETRYABLE,
                ) from exc
            if response.status < 200 or response.status >= 300:
                error_code = self._error_code(payload)
                detail = f": {error_code}" if error_code else ""
                raise CobaltClientError(
                    f"Cobalt API вернул ошибку{detail}",
                    kind=CobaltErrorKind.RETRYABLE,
                )
            return await self._parse_payload(payload, mode=mode)
        finally:
            response.release()

    async def _parse_payload(
        self,
        payload: object,
        *,
        mode: CobaltMode,
    ) -> CobaltMediaResponse:
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
            items: list[CobaltPreparedItem] = []
            photo_index = 0
            video_index = 0
            for raw_item in picker:
                if not isinstance(raw_item, dict):
                    continue
                item_type = raw_item.get("type")
                url_value = raw_item.get("url")
                if item_type not in {"photo", "video"} or not isinstance(url_value, str):
                    continue
                media_kind = MediaKind.PHOTO if item_type == "photo" else MediaKind.VIDEO
                if media_kind is MediaKind.PHOTO:
                    part_key = f"photo_{photo_index:03d}"
                    photo_index += 1
                    fallback_name = f"{part_key}.jpg"
                else:
                    part_key = f"video_{video_index:03d}"
                    video_index += 1
                    fallback_name = f"{part_key}.mp4"
                items.append(
                    CobaltPreparedItem(
                        part_key=part_key,
                        media_kind=media_kind,
                        position=len(items),
                        url=url_value,
                        filename=str(raw_item.get("filename") or fallback_name),
                        response_kind=CobaltResponseKind.PICKER,
                        internal_origin=self.base_url,
                    )
                )
            audio_url = payload.get("audio")
            if isinstance(audio_url, str) and audio_url:
                items.append(
                    CobaltPreparedItem(
                        part_key="audio",
                        media_kind=MediaKind.AUDIO,
                        position=len(items),
                        url=audio_url,
                        filename=str(payload.get("audioFilename") or "audio"),
                        response_kind=CobaltResponseKind.PICKER,
                        internal_origin=self.base_url,
                    )
                )
            if not items:
                raise CobaltClientError(
                    "Cobalt не нашёл поддерживаемые медиа в публикации",
                    kind=CobaltErrorKind.UNSUPPORTED,
                )
        elif status in {"tunnel", "redirect"}:
            url_value = payload.get("url")
            if not isinstance(url_value, str) or not url_value:
                raise CobaltClientError(
                    "Cobalt response не содержит media URL",
                    kind=CobaltErrorKind.RETRYABLE,
                )
            response_kind = CobaltResponseKind(str(status))
            filename = str(payload.get("filename") or "")
            if mode is CobaltMode.AUDIO:
                media_kind = MediaKind.AUDIO
                part_key = "audio"
                fallback_name = "audio"
            elif Path(filename).suffix.lower() == ".gif":
                media_kind = MediaKind.ANIMATION
                part_key = "animation_000"
                fallback_name = "animation_000.gif"
            elif Path(filename).suffix.lower() in {
                ".avif",
                ".jpeg",
                ".jpg",
                ".png",
                ".webp",
            }:
                media_kind = MediaKind.PHOTO
                part_key = "photo_000"
                fallback_name = "photo_000.jpg"
            else:
                media_kind = MediaKind.VIDEO
                part_key = "video"
                fallback_name = "video.mp4"
            items = [
                CobaltPreparedItem(
                    part_key=part_key,
                    media_kind=media_kind,
                    position=0,
                    url=url_value,
                    filename=filename or fallback_name,
                    response_kind=response_kind,
                    internal_origin=(
                        self.base_url if response_kind is CobaltResponseKind.TUNNEL else None
                    ),
                )
            ]
        elif status == "error":
            error_code = self._error_code(payload)
            detail = f": {error_code}" if error_code else ""
            raise CobaltClientError(
                f"Cobalt не получил видео{detail}",
                kind=CobaltErrorKind.RETRYABLE,
            )
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
        for item in items:
            try:
                await self.validator.validate(
                    item.url,
                    allowed_internal_origin=item.internal_origin,
                )
            except UnsafeRemoteUrlError as exc:
                raise CobaltClientError(
                    "Cobalt API вернул небезопасный URL",
                    kind=CobaltErrorKind.SECURITY,
                ) from exc
            except OSError as exc:
                raise CobaltClientError(
                    "Не удалось проверить media URL Cobalt",
                    kind=CobaltErrorKind.RETRYABLE,
                ) from exc
        return CobaltMediaResponse(items=tuple(items))

    @staticmethod
    def _error_code(payload: object) -> str | None:
        if not isinstance(payload, dict):
            return None
        error = payload.get("error")
        if not isinstance(error, dict):
            return None
        code = error.get("code")
        return code if isinstance(code, str) and code else None

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
