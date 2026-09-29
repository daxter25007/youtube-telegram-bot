from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.services.media.models import MediaKind, MediaPackage


@dataclass(frozen=True, slots=True)
class UploadResult:
    message: Message
    sent_as_document: bool


class UnsupportedMediaPackageError(RuntimeError):
    pass


class TelegramFileService:
    async def send(
        self,
        bot: Bot,
        *,
        chat_id: int,
        package: MediaPackage,
        display_filename: str,
        caption: str,
    ) -> UploadResult:
        if len(package.artifacts) != 1:
            raise UnsupportedMediaPackageError(
                "Текущая версия отправляет только один медиафайл за задачу"
            )
        media = package.artifacts[0]
        if media.media_kind is not MediaKind.VIDEO:
            raise UnsupportedMediaPackageError("Текущая версия отправляет только видео")
        # Local Bot API expects the file: URI scheme for server-side filesystem
        # uploads. FSInputFile would stream the entire file through Python.
        local_uri = f"file://{quote(media.path.as_posix(), safe='/')}"
        try:
            message = await bot.send_video(
                chat_id,
                video=local_uri,
                caption=f"{caption}\n📄 {display_filename}",
                duration=media.duration,
                width=media.width,
                height=media.height,
                supports_streaming=True,
            )
            return UploadResult(message=message, sent_as_document=False)
        except TelegramBadRequest:
            message = await bot.send_document(
                chat_id,
                document=local_uri,
                caption=f"{caption}\n📄 {display_filename}",
            )
            return UploadResult(message=message, sent_as_document=True)
