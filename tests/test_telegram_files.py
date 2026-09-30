from pathlib import Path
from types import SimpleNamespace

import pytest
from aiogram.enums import ParseMode

from app.services.media.models import MediaArtifact, MediaKind, MediaPackage
from app.services.telegram_files import (
    TelegramFileService,
    UnsupportedMediaPackageError,
    UploadResult,
)


class FakeBot:
    def __init__(self) -> None:
        self.video: object | None = None
        self.document: object | None = None
        self.video_kwargs: dict[str, object] = {}
        self.document_kwargs: dict[str, object] = {}

    async def send_video(self, chat_id: int, *, video: object, **kwargs: object):
        self.video = video
        self.video_kwargs = kwargs
        return object()

    async def send_document(self, chat_id: int, *, document: object, **kwargs: object):
        self.document = document
        self.document_kwargs = kwargs
        return object()


@pytest.mark.asyncio
async def test_upload_passes_local_file_uri_not_input_file() -> None:
    bot = FakeBot()
    package = MediaPackage(
        artifacts=(
            MediaArtifact(
                path=Path("/shared-temp/jobs/id/media.mp4"),
                media_kind=MediaKind.VIDEO,
                width=1280,
                height=720,
                duration=10,
            ),
        )
    )
    await TelegramFileService().send(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        package=package,
        caption="video",
    )
    assert bot.video == "file:///shared-temp/jobs/id/media.mp4"


@pytest.mark.asyncio
async def test_upload_uses_html_caption_without_filename_line() -> None:
    bot = FakeBot()
    package = MediaPackage(
        artifacts=(MediaArtifact(path=Path("/media.mp4"), media_kind=MediaKind.VIDEO),)
    )

    await TelegramFileService().send(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        package=package,
        caption="🍿 <code>Example</code>",
    )

    assert bot.video_kwargs["caption"] == "🍿 <code>Example</code>"
    assert bot.video_kwargs["parse_mode"] is ParseMode.HTML


@pytest.mark.asyncio
async def test_upload_rejects_multi_artifact_package_until_media_groups_are_supported() -> None:
    artifact = MediaArtifact(path=Path("/one.mp4"), media_kind=MediaKind.VIDEO)
    package = MediaPackage(artifacts=(artifact, artifact))

    with pytest.raises(UnsupportedMediaPackageError):
        await TelegramFileService().send(  # type: ignore[arg-type]
            FakeBot(),
            chat_id=1,
            package=package,
            caption="video",
        )


def test_upload_result_exposes_telegram_file_identifiers() -> None:
    result = UploadResult(
        message=SimpleNamespace(
            video=SimpleNamespace(file_id="file-id", file_unique_id="unique-id"),
            document=None,
        ),
        sent_as_document=False,
    )

    assert result.file_id == "file-id"
    assert result.file_unique_id == "unique-id"


@pytest.mark.asyncio
@pytest.mark.parametrize("sent_as_document", [False, True])
async def test_cached_file_is_resent_using_its_original_telegram_type(
    sent_as_document: bool,
) -> None:
    bot = FakeBot()

    await TelegramFileService().send_cached(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        file_id="cached-file-id",
        sent_as_document=sent_as_document,
        caption="cached",
    )

    if sent_as_document:
        assert bot.document == "cached-file-id"
        assert bot.video is None
        assert bot.document_kwargs["parse_mode"] is ParseMode.HTML
    else:
        assert bot.video == "cached-file-id"
        assert bot.document is None
        assert bot.video_kwargs["parse_mode"] is ParseMode.HTML
