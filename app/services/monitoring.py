from __future__ import annotations

import asyncio
import shutil
from contextlib import suppress
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from aiogram import Bot
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

from app.services.cobalt.client import CobaltClient
from app.services.queue import DownloadQueue
from app.utils.filesize import human_size
from app.utils.logging import recent_log_counts


def _format_uptime(started_at: datetime) -> str:
    seconds = max(0, int((datetime.now(UTC) - started_at).total_seconds()))
    days, remainder = divmod(seconds, 86_400)
    hours, remainder = divmod(remainder, 3_600)
    minutes, _ = divmod(remainder, 60)
    parts = []
    if days:
        parts.append(f"{days} д")
    if hours or days:
        parts.append(f"{hours} ч")
    parts.append(f"{minutes} мин")
    return " ".join(parts)


@dataclass(frozen=True, slots=True)
class MonitoringService:
    started_at: datetime
    log_path: Path

    async def report(
        self,
        *,
        bot: Bot,
        session: AsyncSession,
        cobalt_client: CobaltClient | None,
        download_queue: DownloadQueue,
        temp_root: Path,
    ) -> str:
        database_status = await self._database_status(session)
        telegram_status = await self._telegram_status(bot)
        cobalt_status = await self._cobalt_status(cobalt_client)
        disk_status = await asyncio.to_thread(self._disk_status, temp_root)
        warnings, errors = await asyncio.to_thread(recent_log_counts, self.log_path)
        queue_status = "работает" if download_queue.is_running else "остановлена"
        return (
            "🩺 Мониторинг\n\n"
            f"Бот: ✅ работает · {_format_uptime(self.started_at)}\n"
            f"PostgreSQL: {database_status}\n"
            f"Telegram API: {telegram_status}\n"
            f"Cobalt: {cobalt_status}\n"
            f"Очередь: {queue_status} · активных {download_queue.active_count} · "
            f"ожидают {download_queue.pending_count}\n"
            f"Диск временных файлов: {disk_status}\n"
            f"Последние 500 строк: предупреждений {warnings}, ошибок {errors}"
        )

    @staticmethod
    async def _database_status(session: AsyncSession) -> str:
        try:
            await session.execute(text("SELECT 1"))
        except Exception:
            return "❌ недоступна"
        return "✅ доступна"

    @staticmethod
    async def _telegram_status(bot: Bot) -> str:
        try:
            await bot.get_me()
        except Exception:
            return "❌ недоступен"
        return "✅ доступен"

    @staticmethod
    async def _cobalt_status(client: CobaltClient | None) -> str:
        if client is None:
            return "⚪ отключён"
        try:
            healthy = await client.healthcheck()
        except Exception:
            healthy = False
        return "✅ доступен" if healthy else "❌ недоступен"

    @staticmethod
    def _disk_status(temp_root: Path) -> str:
        try:
            usage = shutil.disk_usage(temp_root)
        except OSError:
            return "❌ недоступен"
        used_percent = (usage.used / usage.total * 100) if usage.total else 0
        return f"свободно {human_size(usage.free)} · занято {used_percent:.1f}%"


async def heartbeat_loop(
    marker: Path,
    *,
    interval_seconds: float,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        await asyncio.to_thread(marker.touch)
        with suppress(TimeoutError):
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
