from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.services.media.models import MediaKind, MediaPackage


@dataclass(frozen=True, slots=True)
class UploadResult:
    message: Message
    sent_as_document: bool

    @property
    def file_id(self) -> str:
        return self._file().file_id

    @property
    def file_unique_id(self) -> str:
        return self._file().file_unique_id

    def _file(self):
        media = self.message.document if self.sent_as_document else self.message.video
        if media is None:
            raise UnsupportedMediaPackageError("Telegram не вернул отправленный файл")
        return media


class UnsupportedMediaPackageError(RuntimeError):
    pass


class TelegramFileService:
    async def send_cached(
        self,
        bot: Bot,
        *,
        chat_id: int,
        file_id: str,
        sent_as_document: bool,
        caption: str,
    ) -> Message:
        if sent_as_document:
            return await bot.send_document(
                chat_id,
                document=file_id,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
        return await bot.send_video(
            chat_id,
            video=file_id,
            caption=caption,
            parse_mode=ParseMode.HTML,
            supports_streaming=True,
        )

    async def send(
        self,
        bot: Bot,
        *,
        chat_id: int,
        package: MediaPackage,
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
                caption=caption,
                parse_mode=ParseMode.HTML,
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
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            return UploadResult(message=message, sent_as_document=True)
