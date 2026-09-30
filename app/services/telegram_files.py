from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from urllib.parse import quote

from aiogram import Bot
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest
from aiogram.types import InputMediaPhoto, Message

from app.services.media.models import MediaArtifact, MediaKind, MediaPackage


@dataclass(frozen=True, slots=True)
class TelegramPhotoInput:
    part_key: str
    media: str
    cached: bool = False


@dataclass(frozen=True, slots=True)
class UploadResult:
    part_key: str
    media_kind: MediaKind
    message: Message
    sent_as_document: bool

    @property
    def file_id(self) -> str:
        return self._file().file_id

    @property
    def file_unique_id(self) -> str:
        return self._file().file_unique_id

    def _file(self):
        if self.sent_as_document:
            media = self.message.document
        elif self.media_kind is MediaKind.VIDEO:
            media = self.message.video
        elif self.media_kind is MediaKind.AUDIO:
            media = self.message.audio
        else:
            photos = self.message.photo
            media = photos[-1] if photos else None
        if media is None:
            raise UnsupportedMediaPackageError("Telegram не вернул отправленный файл")
        return media


class UnsupportedMediaPackageError(RuntimeError):
    pass


class InvalidCachedPartError(RuntimeError):
    def __init__(self, part_key: str) -> None:
        super().__init__(part_key)
        self.part_key = part_key


def _invalid_file_id(error: TelegramBadRequest) -> bool:
    text = str(error).casefold()
    return any(
        marker in text
        for marker in ("wrong file identifier", "invalid file id", "file reference expired")
    )


def _local_uri(media: MediaArtifact) -> str:
    return f"file://{quote(media.path.as_posix(), safe='/')}"


class TelegramFileService:
    async def send_cached(
        self,
        bot: Bot,
        *,
        chat_id: int,
        file_id: str,
        sent_as_document: bool,
        caption: str,
        part_key: str = "media",
        media_kind: MediaKind = MediaKind.VIDEO,
    ) -> UploadResult:
        kwargs = {"caption": caption, "parse_mode": ParseMode.HTML}
        try:
            if sent_as_document:
                message = await bot.send_document(chat_id, document=file_id, **kwargs)
            elif media_kind is MediaKind.VIDEO:
                message = await bot.send_video(
                    chat_id,
                    video=file_id,
                    supports_streaming=True,
                    **kwargs,
                )
            elif media_kind is MediaKind.AUDIO:
                message = await bot.send_audio(chat_id, audio=file_id, **kwargs)
            else:
                message = await bot.send_photo(chat_id, photo=file_id, **kwargs)
        except TelegramBadRequest as error:
            if _invalid_file_id(error):
                raise InvalidCachedPartError(part_key) from error
            raise
        return UploadResult(
            part_key=part_key,
            media_kind=media_kind,
            message=message,
            sent_as_document=sent_as_document,
        )

    async def send_artifacts(
        self,
        bot: Bot,
        *,
        chat_id: int,
        package: MediaPackage,
        caption: str,
    ) -> tuple[UploadResult, ...]:
        results: list[UploadResult] = []
        caption_available = True
        photos = [
            TelegramPhotoInput(
                part_key=artifact.part_key,
                media=_local_uri(artifact),
            )
            for artifact in package.artifacts
            if artifact.media_kind is MediaKind.PHOTO
        ]
        for artifact in package.artifacts:
            if artifact.media_kind is not MediaKind.VIDEO:
                continue
            result = await self._send_video(
                bot,
                chat_id=chat_id,
                media=artifact,
                caption=caption if caption_available else "",
            )
            results.append(result)
            caption_available = False
        if photos:
            photo_results = await self.send_photo_album(
                bot,
                chat_id=chat_id,
                photos=photos,
                caption=caption if caption_available else "",
            )
            results.extend(photo_results)
            caption_available = False
        for artifact in package.artifacts:
            if artifact.media_kind is not MediaKind.AUDIO:
                continue
            result = await self._send_audio(
                bot,
                chat_id=chat_id,
                media=artifact,
                caption=caption if caption_available else "",
            )
            results.append(result)
            caption_available = False
        return tuple(results)

    async def send_photo_album(
        self,
        bot: Bot,
        *,
        chat_id: int,
        photos: Sequence[TelegramPhotoInput],
        caption: str,
    ) -> tuple[UploadResult, ...]:
        results: list[UploadResult] = []
        caption_available = bool(caption)
        for photo in photos:
            if not photo.cached:
                continue
            try:
                await bot.get_file(photo.media)
            except TelegramBadRequest as error:
                if _invalid_file_id(error):
                    raise InvalidCachedPartError(photo.part_key) from error
                raise
        for start in range(0, len(photos), 10):
            batch = photos[start : start + 10]
            if len(batch) == 1:
                message = await bot.send_photo(
                    chat_id,
                    photo=batch[0].media,
                    caption=caption if caption_available else None,
                    parse_mode=ParseMode.HTML,
                )
                messages = [message]
            else:
                media = [
                    InputMediaPhoto(
                        media=photo.media,
                        caption=(caption if caption_available and index == 0 else None),
                        parse_mode=(ParseMode.HTML if caption_available and index == 0 else None),
                    )
                    for index, photo in enumerate(batch)
                ]
                messages = await bot.send_media_group(chat_id, media=media)
            for photo, message in zip(batch, messages, strict=True):
                results.append(
                    UploadResult(
                        part_key=photo.part_key,
                        media_kind=MediaKind.PHOTO,
                        message=message,
                        sent_as_document=False,
                    )
                )
            caption_available = False
        return tuple(results)

    async def send(
        self,
        bot: Bot,
        *,
        chat_id: int,
        package: MediaPackage,
        caption: str,
    ) -> UploadResult:
        results = await self.send_artifacts(bot, chat_id=chat_id, package=package, caption=caption)
        if len(results) != 1:
            raise UnsupportedMediaPackageError("Ожидался один результат отправки")
        return results[0]

    async def _send_video(
        self,
        bot: Bot,
        *,
        chat_id: int,
        media: MediaArtifact,
        caption: str,
    ) -> UploadResult:
        local_uri = _local_uri(media)
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
            sent_as_document = False
        except TelegramBadRequest:
            message = await bot.send_document(
                chat_id,
                document=local_uri,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            sent_as_document = True
        return UploadResult(
            part_key=media.part_key,
            media_kind=MediaKind.VIDEO,
            message=message,
            sent_as_document=sent_as_document,
        )

    async def _send_audio(
        self,
        bot: Bot,
        *,
        chat_id: int,
        media: MediaArtifact,
        caption: str,
    ) -> UploadResult:
        local_uri = _local_uri(media)
        if media.path.suffix.lower() in {".mp3", ".m4a"}:
            message = await bot.send_audio(
                chat_id,
                audio=local_uri,
                caption=caption,
                parse_mode=ParseMode.HTML,
                duration=media.duration,
            )
            sent_as_document = False
        else:
            message = await bot.send_document(
                chat_id,
                document=local_uri,
                caption=caption,
                parse_mode=ParseMode.HTML,
            )
            sent_as_document = True
        return UploadResult(
            part_key=media.part_key,
            media_kind=MediaKind.AUDIO,
            message=message,
            sent_as_document=sent_as_document,
        )
