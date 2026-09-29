from pathlib import Path

import pytest

from app.services.telegram_files import TelegramFileService
from app.services.youtube.downloader import DownloadedMedia


class FakeBot:
    def __init__(self) -> None:
        self.video: object | None = None

    async def send_video(self, chat_id: int, *, video: object, **kwargs: object):
        self.video = video
        return object()


@pytest.mark.asyncio
async def test_upload_passes_local_file_uri_not_input_file() -> None:
    bot = FakeBot()
    media = DownloadedMedia(
        path=Path("/shared-temp/jobs/id/media.mp4"),
        width=1280,
        height=720,
        duration=10,
    )
    await TelegramFileService().send(  # type: ignore[arg-type]
        bot,
        chat_id=1,
        media=media,
        display_filename="video.mp4",
        caption="video",
    )
    assert bot.video == "file:///shared-temp/jobs/id/media.mp4"
