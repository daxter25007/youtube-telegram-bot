from __future__ import annotations

from dataclasses import dataclass

from aiogram import Bot
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import Message

from app.services.youtube.downloader import DownloadedMedia


@dataclass(frozen=True, slots=True)
class UploadResult:
    message: Message
    sent_as_document: bool


class TelegramFileService:
    async def send(
        self,
        bot: Bot,
        *,
        chat_id: int,
        media: DownloadedMedia,
        display_filename: str,
        caption: str,
    ) -> UploadResult:
        # A string absolute path is passed directly to the local Bot API. FSInputFile
        # would make aiogram stream the entire file as multipart through Python.
        local_path = media.path.as_posix()
        try:
            message = await bot.send_video(
                chat_id,
                video=local_path,
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
                document=local_path,
                caption=f"{caption}\n📄 {display_filename}",
            )
            return UploadResult(message=message, sent_as_document=True)
