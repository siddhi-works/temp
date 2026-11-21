"""Background worker and scheduler, run inside the API process (one deployable).

For scale, run the same loop in a separate process with WORKER_ENABLED=false on
the API: the queue is in the database, so nothing else changes.
"""

from __future__ import annotations

import asyncio
import logging

from ..config import get_settings
from ..errors import PipelineError
from ..providers.base import ProviderError
from . import jobs
from .steps import HANDLERS, enqueue_due_reminders, on_job_dead

log = logging.getLogger("suchana.worker")


async def run_job(job) -> None:
    fn = HANDLERS.get(job.type)
    if fn is None:
        await jobs.fail(job, f"no handler for job type {job.type}", retryable=False)
        return
    try:
        await fn(job)
    except (PipelineError, ProviderError) as exc:
        log.warning("job %s (%s) attempt %d failed: %s", job.id, job.type, job.attempts, exc)
        if await jobs.fail(job, str(exc), retryable=exc.retryable):
            await on_job_dead(job, exc)
        return
    except Exception as exc:
        log.exception("job %s (%s) crashed", job.id, job.type)
        if await jobs.fail(job, f"{type(exc).__name__}: {exc}", retryable=True):
            await on_job_dead(job, exc)
        return
    await jobs.complete(job.id)


class Worker:
    def __init__(self) -> None:
        self._stop = asyncio.Event()
        self._tasks: list[asyncio.Task] = []
        self.last_tick: float | None = None

    async def _loop(self, n: int) -> None:
        poll = get_settings().worker_poll_seconds
        while not self._stop.is_set():
            try:
                job = await jobs.claim_one()
            except Exception:
                log.exception("claim failed")
                job = None
                await asyncio.sleep(poll * 5)
            self.last_tick = asyncio.get_running_loop().time()
            if job is None:
                try:
                    await asyncio.wait_for(self._stop.wait(), timeout=poll)
                except asyncio.TimeoutError:
                    pass
                continue
            await run_job(job)

    async def _scheduler(self) -> None:
        while not self._stop.is_set():
            try:
                n = await enqueue_due_reminders()
                if n:
                    log.info("enqueued %d reminders", n)
            except Exception:
                log.exception("scheduler tick failed")
            try:
                await asyncio.wait_for(self._stop.wait(), timeout=30)
            except asyncio.TimeoutError:
                pass

    def start(self) -> None:
        n = get_settings().worker_concurrency
        self._tasks = [asyncio.create_task(self._loop(i), name=f"worker-{i}") for i in range(n)]
        self._tasks.append(asyncio.create_task(self._scheduler(), name="scheduler"))
        log.info("worker started with %d loops", n)

    async def stop(self, timeout: float = 20) -> None:
        """Graceful: stop claiming, let running jobs finish; unfinished ones resume via lease expiry."""
        self._stop.set()
        if self._tasks:
            done, pending = await asyncio.wait(self._tasks, timeout=timeout)
            for t in pending:
                t.cancel()

    @property
    def running(self) -> bool:
        return any(not t.done() for t in self._tasks)


async def drain(max_jobs: int = 100) -> int:
    """Run queued jobs inline until none are ready. Used by tests and the CLI."""
    count = 0
    while count < max_jobs:
        job = await jobs.claim_one()
        if job is None:
            break
        await run_job(job)
        count += 1
    return count
