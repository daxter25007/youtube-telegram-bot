from types import SimpleNamespace

import pytest

from app.services.download_manager import ProgressReporter, _downloading_text
from app.services.media.downloader import ProgressStats


class FakeMessage:
    def __init__(self) -> None:
        self.texts: list[str] = []

    async def edit_text(self, text: str) -> None:
        self.texts.append(text)


def test_downloading_text_with_full_stats() -> None:
    stats = ProgressStats(downloaded_bytes=5242880, total_bytes=68157440, speed_bytes=131072)
    assert _downloading_text(7.7, stats) == "⬇️ Скачиваю видео: 8% · 5.0 МБ из 65.0 МБ · 128.0 КБ/с"


def test_downloading_text_percent_only() -> None:
    assert _downloading_text(41.2, None) == "⬇️ Скачиваю видео: 41%"


def test_downloading_text_with_unknown_fields() -> None:
    stats = ProgressStats(downloaded_bytes=5767168, total_bytes=68157440, speed_bytes=None)
    assert _downloading_text(40.0, stats) == "⬇️ Скачиваю видео: 40% · 5.5 МБ из 65.0 МБ"


def test_downloading_text_without_any_data() -> None:
    assert _downloading_text(None, None) == "⬇️ Скачиваю видео..."


@pytest.mark.asyncio
async def test_reporter_updates_text_when_only_bytes_change() -> None:
    # Процент «застыл», но байты растут — текст должен меняться,
    # чтобы пользователь отличал медленную загрузку от зависшей.
    message = FakeMessage()
    reporter = ProgressReporter(SimpleNamespace(), min_interval=0.0)
    reporter.message = message  # type: ignore[assignment]
    stats = ProgressStats(downloaded_bytes=1000, total_bytes=1_000_000, speed_bytes=500)
    await reporter.update("downloading", 0.0, stats)
    await reporter.update("downloading", 0.0, stats)
    grown = ProgressStats(downloaded_bytes=2000, total_bytes=1_000_000, speed_bytes=500)
    await reporter.update("downloading", 0.0, grown)
    assert message.texts == [
        "⬇️ Скачиваю видео: 0% · 1000 Б из 976.6 КБ · 500 Б/с",
        "⬇️ Скачиваю видео: 0% · 2.0 КБ из 976.6 КБ · 500 Б/с",
    ]


@pytest.mark.asyncio
async def test_reporter_skips_duplicate_text() -> None:
    message = FakeMessage()
    reporter = ProgressReporter(SimpleNamespace(), min_interval=0.0)
    reporter.message = message  # type: ignore[assignment]
    stats = ProgressStats(downloaded_bytes=10, total_bytes=None, speed_bytes=None)
    await reporter.update("downloading", 5.0, stats)
    await reporter.update("downloading", 5.0, stats)
    assert message.texts == ["⬇️ Скачиваю видео: 5% · 10 Б"]


@pytest.mark.asyncio
async def test_reporter_respects_min_interval() -> None:
    message = FakeMessage()
    reporter = ProgressReporter(SimpleNamespace(), min_interval=60.0)
    reporter.message = message  # type: ignore[assignment]
    stats = ProgressStats(downloaded_bytes=10)
    await reporter.update("downloading", 5.0, stats)
    await reporter.update("downloading", 6.0, stats)
    await reporter.update("downloading", 7.0, stats, force=True)
    assert message.texts == [
        "⬇️ Скачиваю видео: 5% · 10 Б",
        "⬇️ Скачиваю видео: 7% · 10 Б",
    ]
