from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.enums import ParseMode
from aiogram.exceptions import TelegramBadRequest

from app.services.media.models import MediaArtifact, MediaKind, MediaPackage
from app.services.telegram_files import TelegramFileService, TelegramPhotoInput, UploadResult


def telegram_message(kind: MediaKind, file_id: str):
    file = SimpleNamespace(file_id=file_id, file_unique_id=f"unique-{file_id}")
    return SimpleNamespace(
        video=file if kind is MediaKind.VIDEO else None,
        audio=file if kind is MediaKind.AUDIO else None,
        photo=[file] if kind is MediaKind.PHOTO else None,
        document=None,
    )


class FakeBot:
    def __init__(self, *, reject_video: bool = False) -> None:
        self.reject_video = reject_video
        self.calls: list[tuple[str, object, dict[str, object]]] = []

    async def send_video(self, chat_id: int, *, video: object, **kwargs: object):
        self.calls.append(("video", video, kwargs))
        if self.reject_video:
            raise TelegramBadRequest(method=SimpleNamespace(), message="bad video")
        return telegram_message(MediaKind.VIDEO, "video-id")

    async def send_audio(self, chat_id: int, *, audio: object, **kwargs: object):
        self.calls.append(("audio", audio, kwargs))
        return telegram_message(MediaKind.AUDIO, "audio-id")

    async def send_document(self, chat_id: int, *, document: object, **kwargs: object):
        self.calls.append(("document", document, kwargs))
        message = telegram_message(MediaKind.VIDEO, "unused")
        message.video = None
        message.document = SimpleNamespace(file_id="document-id", file_unique_id="unique-document")
        return message

    async def send_photo(self, chat_id: int, *, photo: object, **kwargs: object):
        self.calls.append(("photo", photo, kwargs))
        return telegram_message(MediaKind.PHOTO, f"photo-{len(self.calls)}")

    async def send_media_group(self, chat_id: int, *, media: list[object], **kwargs: object):
        self.calls.append(("media_group", media, kwargs))
        return [telegram_message(MediaKind.PHOTO, f"group-{index}") for index in range(len(media))]


@pytest.mark.asyncio
async def test_video_uses_local_file_uri_and_falls_back_to_document() -> None:
    bot = FakeBot(reject_video=True)
    package = MediaPackage(
        artifacts=(
            MediaArtifact(
                path=Path("/shared-temp/jobs/id/video.mp4"),
                media_kind=MediaKind.VIDEO,
                part_key="video",
            ),
        )
    )

    results = await TelegramFileService().send_artifacts(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        package=package,
        caption="video",
    )

    assert bot.calls[0][1] == "file:///shared-temp/jobs/id/video.mp4"
    assert bot.calls[1][0] == "document"
    assert results[0].part_key == "video"
    assert results[0].sent_as_document is True


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("suffix", "method"),
    [(".m4a", "audio"), (".mp3", "audio"), (".opus", "document"), (".ogg", "document")],
)
async def test_audio_container_selects_player_or_document(suffix: str, method: str) -> None:
    bot = FakeBot()
    package = MediaPackage(
        artifacts=(
            MediaArtifact(
                path=Path(f"/media/audio{suffix}"),
                media_kind=MediaKind.AUDIO,
                part_key="audio",
            ),
        )
    )

    results = await TelegramFileService().send_artifacts(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        package=package,
        caption="sound",
    )

    assert bot.calls[0][0] == method
    assert results[0].sent_as_document is (method == "document")


@pytest.mark.asyncio
async def test_single_photo_uses_send_photo() -> None:
    bot = FakeBot()
    package = MediaPackage(
        artifacts=(
            MediaArtifact(
                path=Path("/media/photo.jpg"),
                media_kind=MediaKind.PHOTO,
                part_key="photo_000",
            ),
        )
    )

    results = await TelegramFileService().send_artifacts(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        package=package,
        caption="photo",
    )

    assert bot.calls[0][0] == "photo"
    assert results[0].media_kind is MediaKind.PHOTO


@pytest.mark.asyncio
async def test_eleven_mixed_photos_keep_order_across_group_and_single() -> None:
    bot = FakeBot()
    photos = tuple(
        TelegramPhotoInput(
            part_key=f"photo_{index:03d}",
            media=(f"cached-{index}" if index % 2 else f"file:///media/{index}.jpg"),
            cached=index % 2 == 1,
        )
        for index in range(11)
    )

    results = await TelegramFileService().send_photo_album(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        photos=photos,
        caption="carousel",
    )

    assert [call[0] for call in bot.calls] == ["media_group", "photo"]
    first_batch = bot.calls[0][1]
    assert [item.media for item in first_batch] == [photo.media for photo in photos[:10]]
    assert first_batch[0].caption == "carousel"
    assert all(item.caption is None for item in first_batch[1:])
    assert bot.calls[1][1] == photos[10].media
    assert bot.calls[1][2].get("caption") is None
    assert [result.part_key for result in results] == [photo.part_key for photo in photos]


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("kind", "sent_as_document", "method"),
    [
        (MediaKind.VIDEO, False, "video"),
        (MediaKind.AUDIO, False, "audio"),
        (MediaKind.PHOTO, False, "photo"),
        (MediaKind.AUDIO, True, "document"),
    ],
)
async def test_cached_file_is_resent_using_original_type(
    kind: MediaKind,
    sent_as_document: bool,
    method: str,
) -> None:
    bot = FakeBot()

    result = await TelegramFileService().send_cached(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        part_key="part",
        media_kind=kind,
        file_id="cached-file-id",
        sent_as_document=sent_as_document,
        caption="cached",
    )

    assert bot.calls[0][0] == method
    assert bot.calls[0][2]["parse_mode"] is ParseMode.HTML
    assert result.part_key == "part"


def test_upload_result_reads_file_for_each_media_kind() -> None:
    for kind in MediaKind:
        result = UploadResult(
            part_key=kind.value,
            media_kind=kind,
            message=telegram_message(kind, f"{kind.value}-id"),
            sent_as_document=False,
        )
        assert result.file_id == f"{kind.value}-id"
        assert result.file_unique_id == f"unique-{kind.value}-id"
