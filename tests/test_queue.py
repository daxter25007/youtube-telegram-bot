import asyncio

import pytest

from app.services.queue import DownloadQueue, UserDownloadLimitError


@pytest.mark.asyncio
async def test_queue_limits_global_concurrency_and_per_user() -> None:
    queue = DownloadQueue(concurrency=2, max_per_user=1)
    await queue.start()
    running = 0
    maximum = 0
    release = asyncio.Event()

    async def job() -> None:
        nonlocal running, maximum
        running += 1
        maximum = max(maximum, running)
        await release.wait()
        running -= 1

    try:
        assert await queue.submit(1, job) == 1
        assert await queue.submit(2, job) == 2
        assert await queue.submit(3, job) == 3
        with pytest.raises(UserDownloadLimitError):
            await queue.submit(1, job)
        await asyncio.sleep(0)
        assert maximum == 2
        release.set()
        await asyncio.wait_for(queue._queue.join(), timeout=1)
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_queue_calls_cancel_hook_for_pending_jobs() -> None:
    queue = DownloadQueue(concurrency=1, max_per_user=1)
    await queue.start()
    started = asyncio.Event()
    release = asyncio.Event()
    cancelled = asyncio.Event()

    async def active() -> None:
        started.set()
        await release.wait()

    async def pending() -> None:
        raise AssertionError("pending job must not run")

    await queue.submit(1, active)
    await started.wait()
    await queue.submit(2, pending, on_cancel=lambda: _set_event(cancelled))
    await queue.stop()
    assert cancelled.is_set()


async def _set_event(event: asyncio.Event) -> None:
    event.set()
