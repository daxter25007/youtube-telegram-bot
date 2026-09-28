from __future__ import annotations

import asyncio
import logging
import shutil
import time
import uuid
from pathlib import Path

from sqlalchemy.exc import SQLAlchemyError

from app.database.repositories.requests import DownloadRequestRepository
from app.database.session import Database

logger = logging.getLogger(__name__)


class TempStorage:
    def __init__(self, root: Path) -> None:
        self.root = root.resolve()
        self._active: set[Path] = set()
        self._lock = asyncio.Lock()

    async def initialize(self) -> None:
        await asyncio.to_thread(self.root.mkdir, parents=True, exist_ok=True, mode=0o755)

    async def create_job(self) -> Path:
        await self.initialize()
        path = (self.root / str(uuid.uuid4())).resolve()
        if path.parent != self.root:
            raise RuntimeError("Invalid job path")
        await asyncio.to_thread(path.mkdir, mode=0o755)
        async with self._lock:
            self._active.add(path)
        return path

    async def remove_job(self, path: Path) -> None:
        resolved = await asyncio.to_thread(path.resolve)
        async with self._lock:
            self._active.discard(resolved)

        def remove() -> None:
            if resolved.parent == self.root and resolved.exists():
                shutil.rmtree(resolved, ignore_errors=True)

        await asyncio.to_thread(remove)

    async def cleanup_stale(self, ttl_hours: int) -> int:
        await self.initialize()
        cutoff = time.time() - ttl_hours * 3600
        async with self._lock:
            active = self._active.copy()

        def cleanup() -> int:
            removed = 0
            for child in self.root.iterdir():
                try:
                    resolved = child.resolve()
                    uuid.UUID(child.name)
                    if (
                        child.is_dir()
                        and not child.is_symlink()
                        and resolved not in active
                        and child.stat().st_mtime < cutoff
                    ):
                        shutil.rmtree(child)
                        removed += 1
                except (OSError, ValueError):
                    logger.warning("Unable to inspect stale temp entry %s", child.name)
            return removed

        return await asyncio.to_thread(cleanup)


async def cleanup_loop(
    storage: TempStorage,
    *,
    ttl_hours: int,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            try:
                removed = await storage.cleanup_stale(ttl_hours)
                if removed:
                    logger.info("Removed %d stale temporary job directories", removed)
            except OSError:
                logger.exception("Periodic temporary file cleanup failed")


async def request_cleanup_loop(
    database: Database,
    *,
    ttl_minutes: int,
    interval_seconds: int,
    stop_event: asyncio.Event,
) -> None:
    while not stop_event.is_set():
        try:
            await asyncio.wait_for(stop_event.wait(), timeout=interval_seconds)
        except TimeoutError:
            try:
                async with database.session() as session:
                    removed = await DownloadRequestRepository(session).cleanup_expired(ttl_minutes)
                if removed:
                    logger.info("Removed %d expired download requests", removed)
            except SQLAlchemyError:
                logger.exception("Periodic expired request cleanup failed")
