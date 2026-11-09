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
        # Job-ы исполняются в дочерних задачах — даём событийному циклу время.
        for _ in range(200):
            if maximum == 2:
                break
            await asyncio.sleep(0.01)
        assert maximum == 2
        release.set()
        await asyncio.wait_for(queue._queue.join(), timeout=2)
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


@pytest.mark.asyncio
async def test_cancel_user_job_cancels_running_job_and_frees_slot() -> None:
    queue = DownloadQueue(concurrency=1, max_per_user=1)
    await queue.start()
    started = asyncio.Event()
    cancelled_in_job = asyncio.Event()
    finished = asyncio.Event()

    async def job() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled_in_job.set()
            raise
        finally:
            finished.set()

    try:
        await queue.submit(7, job)
        await started.wait()
        assert await queue.cancel_user_job(7) is True
        await asyncio.wait_for(cancelled_in_job.wait(), timeout=2)
        await asyncio.wait_for(queue._queue.join(), timeout=2)
        # Слот освободился: новая загрузка принимается без ошибки лимита.
        await queue.submit(7, _set_noop)
        await asyncio.wait_for(queue._queue.join(), timeout=2)
    finally:
        await queue.stop()
    assert finished.is_set()


async def _set_noop() -> None:
    return None


@pytest.mark.asyncio
async def test_cancel_user_job_without_active_job_returns_false() -> None:
    queue = DownloadQueue(concurrency=1, max_per_user=1)
    await queue.start()
    try:
        assert await queue.cancel_user_job(42) is False
    finally:
        await queue.stop()


@pytest.mark.asyncio
async def test_worker_keeps_processing_after_user_cancel() -> None:
    queue = DownloadQueue(concurrency=1, max_per_user=1)
    await queue.start()
    started = asyncio.Event()
    cancelled_in_job = asyncio.Event()
    ran_after = asyncio.Event()

    async def cancellable() -> None:
        started.set()
        try:
            await asyncio.sleep(30)
        except asyncio.CancelledError:
            cancelled_in_job.set()
            raise

    async def following() -> None:
        ran_after.set()

    try:
        await queue.submit(9, cancellable)
        await started.wait()
        await queue.cancel_user_job(9)
        await asyncio.wait_for(cancelled_in_job.wait(), timeout=2)
        # Ждём, пока воркер завершит обработку отменённого job (освободит слот).
        await asyncio.wait_for(queue._queue.join(), timeout=2)
        await queue.submit(9, following)
        await asyncio.wait_for(ran_after.wait(), timeout=2)
        await asyncio.wait_for(queue._queue.join(), timeout=2)
    finally:
        await queue.stop()
