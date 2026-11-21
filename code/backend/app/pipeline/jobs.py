"""Durable job queue stored in the database.

Why not an in-memory queue or Celery: jobs must survive a crash or redeploy in
the middle of a demo, and we do not want Redis as another moving part.

- Claiming uses a lease (`locked_until`). If a worker dies, the lease expires and
  another worker picks the job up again.
- On Postgres, claims use FOR UPDATE SKIP LOCKED so several workers never take
  the same job.
- Failures retry with exponential backoff; after max_attempts the job is `dead`
  and the notice shows a clear error plus a Retry button.
- `dedupe_key` makes enqueueing idempotent (e.g. one delivery job per student
  per notice even if "publish" is pressed twice).
"""

from __future__ import annotations

import logging
import os
import random
import socket
from datetime import timedelta

from sqlalchemy import or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import is_postgres, sessionmaker, utcnow
from ..models import Job, JobStatus

log = logging.getLogger("suchana.jobs")
WORKER_ID = f"{socket.gethostname()}:{os.getpid()}"


async def enqueue(
    session: AsyncSession,
    type_: str,
    payload: dict,
    *,
    dedupe_key: str | None = None,
    delay_seconds: float = 0,
    notice_id: str | None = None,
    max_attempts: int | None = None,
) -> Job | None:
    """Add a job inside the caller's transaction. Returns None if a job with this dedupe_key exists."""
    if dedupe_key:
        existing = await session.scalar(select(Job).where(Job.dedupe_key == dedupe_key))
        if existing:
            return None
    job = Job(
        type=type_,
        payload=payload,
        dedupe_key=dedupe_key,
        notice_id=notice_id,
        run_after=utcnow() + timedelta(seconds=delay_seconds),
        max_attempts=max_attempts or get_settings().job_max_attempts,
    )
    session.add(job)
    # The existence check above covers the normal case; the UNIQUE constraint is the
    # backstop for a true race and surfaces as an IntegrityError at the caller's commit.
    return job


async def claim_one() -> Job | None:
    now = utcnow()
    lease = timedelta(seconds=get_settings().job_lease_seconds)
    async with sessionmaker()() as s:
        stmt = (
            select(Job)
            .where(
                or_(
                    (Job.status == JobStatus.queued) & (Job.run_after <= now),
                    # Lease expired: the previous worker crashed mid-job.
                    (Job.status == JobStatus.running) & (Job.locked_until < now),
                )
            )
            .order_by(Job.run_after)
            .limit(1)
        )
        if is_postgres():
            stmt = stmt.with_for_update(skip_locked=True)
        job = await s.scalar(stmt)
        if job is None:
            return None
        # Compare-and-set so two SQLite workers cannot both claim it.
        res = await s.execute(
            update(Job)
            .where(Job.id == job.id, Job.updated_at == job.updated_at)
            .values(
                status=JobStatus.running,
                attempts=Job.attempts + 1,
                locked_until=now + lease,
                locked_by=WORKER_ID,
                updated_at=now,
            )
        )
        if res.rowcount != 1:
            await s.rollback()
            return None
        await s.commit()
        await s.refresh(job)
        return job


async def complete(job_id: str) -> None:
    async with sessionmaker()() as s:
        await s.execute(
            update(Job).where(Job.id == job_id).values(status=JobStatus.done, locked_until=None, last_error=None, updated_at=utcnow())
        )
        await s.commit()


def backoff_seconds(attempt: int) -> float:
    # 5s, 20s, 80s, 5m, 20m (+/- 20% jitter)
    return min(5 * (4 ** (attempt - 1)), 1200) * (0.8 + random.random() * 0.4)


async def fail(job: Job, error: str, *, retryable: bool) -> bool:
    """Record a failure. Returns True if the job is now dead."""
    dead = (not retryable) or job.attempts >= job.max_attempts
    async with sessionmaker()() as s:
        await s.execute(
            update(Job)
            .where(Job.id == job.id)
            .values(
                status=JobStatus.dead if dead else JobStatus.queued,
                run_after=utcnow() + timedelta(seconds=0 if dead else backoff_seconds(job.attempts)),
                locked_until=None,
                last_error=error[:2000],
                updated_at=utcnow(),
            )
        )
        await s.commit()
    return dead


async def requeue_dead_for_notice(session: AsyncSession, notice_id: str) -> int:
    res = await session.execute(
        update(Job)
        .where(Job.notice_id == notice_id, Job.status == JobStatus.dead)
        .values(status=JobStatus.queued, attempts=0, run_after=utcnow(), last_error=None, updated_at=utcnow())
    )
    return res.rowcount or 0
