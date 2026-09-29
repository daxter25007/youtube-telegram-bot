from types import SimpleNamespace

from app.bot.routers.admin import _download_summary_line
from app.database.models import DownloadStatus
from app.services.media.models import MediaSource


def test_admin_download_summary_uses_tiktok_option_instead_of_none_quality() -> None:
    download = SimpleNamespace(
        id=7,
        source=MediaSource.TIKTOK,
        requested_option="best_video",
        requested_quality=None,
        status=DownloadStatus.COMPLETED,
    )

    assert _download_summary_line(download) == "#7 TikTok · лучшее качество — completed"
