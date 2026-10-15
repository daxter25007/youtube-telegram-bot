from types import SimpleNamespace

import pytest

from app.bot.routers.admin import _cobalt_status_text, _download_summary_line
from app.database.models import DownloadStatus
from app.services.media.backends.base import DownloadBackend
from app.services.media.models import MediaSource


def test_admin_download_summary_uses_tiktok_option_instead_of_none_quality() -> None:
    download = SimpleNamespace(
        id=7,
        source=MediaSource.TIKTOK,
        requested_option="best_video",
        requested_quality=None,
        status=DownloadStatus.COMPLETED,
        backend=DownloadBackend.COBALT,
    )

    assert _download_summary_line(download) == ("#7 TikTok · лучшее качество · cobalt — completed")


def test_admin_download_summary_names_instagram_source() -> None:
    download = SimpleNamespace(
        id=8,
        source=MediaSource.INSTAGRAM,
        requested_option="instagram_bundle",
        requested_quality=None,
        status=DownloadStatus.COMPLETED,
        backend=DownloadBackend.COBALT,
    )

    assert _download_summary_line(download) == (
        "#8 Instagram · instagram bundle · cobalt — completed"
    )


def test_admin_download_summary_names_pinterest_source() -> None:
    download = SimpleNamespace(
        id=9,
        source=MediaSource.PINTEREST,
        requested_option="pinterest_bundle",
        requested_quality=None,
        status=DownloadStatus.COMPLETED,
        backend=DownloadBackend.COBALT,
    )

    assert _download_summary_line(download) == (
        "#9 Pinterest · pinterest bundle · cobalt — completed"
    )


class FakeCobaltClient:
    def __init__(self, healthy: bool) -> None:
        self.healthy = healthy

    async def healthcheck(self) -> bool:
        return self.healthy


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("client", "expected"),
    [
        (None, "отключён"),
        (FakeCobaltClient(True), "доступен"),
        (FakeCobaltClient(False), "недоступен"),
    ],
)
async def test_cobalt_admin_status(client, expected: str) -> None:
    assert await _cobalt_status_text(client) == expected
