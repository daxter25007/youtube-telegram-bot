from __future__ import annotations

import asyncio
import logging
from collections import defaultdict
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

JobCallable = Callable[[], Awaitable[None]]
CancelCallable = Callable[[], Awaitable[None]]
logger = logging.getLogger(__name__)


class QueueClosedError(RuntimeError):
    pass


class UserDownloadLimitError(RuntimeError):
    pass


@dataclass(slots=True)
class QueueJob:
    user_id: int
    run: JobCallable
    on_cancel: CancelCallable | None = None


class DownloadQueue:
    def __init__(self, concurrency: int, max_per_user: int) -> None:
        self.concurrency = concurrency
        self.max_per_user = max_per_user
        self._queue: asyncio.Queue[QueueJob | None] = asyncio.Queue()
        self._semaphore = asyncio.Semaphore(concurrency)
        self._user_counts: dict[int, int] = defaultdict(int)
        self._workers: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()
        self._accepting = False

    @property
    def pending_count(self) -> int:
        return self._queue.qsize()

    @property
    def active_count(self) -> int:
        return max(0, sum(self._user_counts.values()) - self.pending_count)

    @property
    def is_running(self) -> bool:
        return self._accepting and bool(self._workers)

    async def start(self) -> None:
        if self._workers:
            return
        self._accepting = True
        self._workers = [
            asyncio.create_task(self._worker(), name=f"download-worker-{index}")
            for index in range(self.concurrency)
        ]

    async def submit(
        self,
        user_id: int,
        run: JobCallable,
        on_cancel: CancelCallable | None = None,
    ) -> int:
        async with self._lock:
            if not self._accepting:
                raise QueueClosedError
            if self._user_counts[user_id] >= self.max_per_user:
                raise UserDownloadLimitError
            position = sum(self._user_counts.values()) + 1
            self._user_counts[user_id] += 1
            await self._queue.put(QueueJob(user_id=user_id, run=run, on_cancel=on_cancel))
            return position

    async def _worker(self) -> None:
        while True:
            job = await self._queue.get()
            if job is None:
                self._queue.task_done()
                return
            try:
                async with self._semaphore:
                    await job.run()
            except Exception:
                logger.exception("Unhandled exception in queued download job")
            finally:
                async with self._lock:
                    self._user_counts[job.user_id] -= 1
                    if self._user_counts[job.user_id] <= 0:
                        self._user_counts.pop(job.user_id, None)
                self._queue.task_done()

    async def stop(self) -> None:
        self._accepting = False
        while not self._queue.empty():
            try:
                job = self._queue.get_nowait()
            except asyncio.QueueEmpty:
                break
            if job is not None:
                async with self._lock:
                    self._user_counts[job.user_id] -= 1
                    if self._user_counts[job.user_id] <= 0:
                        self._user_counts.pop(job.user_id, None)
                if job.on_cancel:
                    try:
                        await job.on_cancel()
                    except Exception:
                        logger.exception("Unable to mark queued job as cancelled")
            self._queue.task_done()
        for worker in self._workers:
            worker.cancel()
        await asyncio.gather(*self._workers, return_exceptions=True)
        self._workers.clear()
