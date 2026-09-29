from pathlib import Path

import pytest

from app.services.media.models import MediaArtifact, MediaKind, MediaPackage
from app.services.telegram_files import TelegramFileService, UnsupportedMediaPackageError


class FakeBot:
    def __init__(self) -> None:
        self.video: object | None = None

    async def send_video(self, chat_id: int, *, video: object, **kwargs: object):
        self.video = video
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
        display_filename="video.mp4",
        caption="video",
    )
    assert bot.video == "file:///shared-temp/jobs/id/media.mp4"


@pytest.mark.asyncio
async def test_upload_rejects_multi_artifact_package_until_media_groups_are_supported() -> None:
    artifact = MediaArtifact(path=Path("/one.mp4"), media_kind=MediaKind.VIDEO)
    package = MediaPackage(artifacts=(artifact, artifact))

    with pytest.raises(UnsupportedMediaPackageError):
        await TelegramFileService().send(  # type: ignore[arg-type]
            FakeBot(),
            chat_id=1,
            package=package,
            display_filename="video.mp4",
            caption="video",
        )
