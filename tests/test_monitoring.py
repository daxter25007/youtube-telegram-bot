import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

import pytest

from app.services.monitoring import MonitoringService, heartbeat_loop


class FakeSession:
    async def execute(self, statement) -> None:
        return None


class FakeBot:
    async def get_me(self) -> SimpleNamespace:
        return SimpleNamespace(id=1)


class FakeCobalt:
    async def healthcheck(self) -> bool:
        return True


@pytest.mark.asyncio
async def test_monitoring_report_contains_live_dependencies(tmp_path: Path) -> None:
    log_path = tmp_path / "bot.log"
    log_path.write_text("2026-01-01 WARNING test: warning\n", encoding="utf-8")
    queue = SimpleNamespace(is_running=True, active_count=2, pending_count=1)
    service = MonitoringService(
        started_at=datetime.now(UTC) - timedelta(minutes=5),
        log_path=log_path,
    )

    report = await service.report(
        bot=FakeBot(),  # type: ignore[arg-type]
        session=FakeSession(),  # type: ignore[arg-type]
        cobalt_client=FakeCobalt(),  # type: ignore[arg-type]
        download_queue=queue,  # type: ignore[arg-type]
        temp_root=tmp_path,
    )

    assert "PostgreSQL: ✅ доступна" in report
    assert "Telegram API: ✅ доступен" in report
    assert "Cobalt: ✅ доступен" in report
    assert "активных 2 · ожидают 1" in report
    assert "предупреждений 1, ошибок 0" in report


@pytest.mark.asyncio
async def test_heartbeat_updates_marker(tmp_path: Path) -> None:
    marker = tmp_path / "ready"
    stop_event = asyncio.Event()
    task = asyncio.create_task(heartbeat_loop(marker, interval_seconds=0.01, stop_event=stop_event))
    await asyncio.sleep(0.03)
    stop_event.set()
    await task

    assert marker.exists()
