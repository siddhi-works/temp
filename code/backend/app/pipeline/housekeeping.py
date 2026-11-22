"""Housekeeping for anonymous sessions: note that a student is still around, and delete sessions nobody has used for days.

There is no "last seen" column and the live database cannot be altered safely (tables are created at start-up, never changed),
so a website-only student's `last_inbound_at` is reused as "last used". That column otherwise means "last WhatsApp message",
which only matters for students with a phone number; website-only students have none, so nothing else reads it for them.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import timedelta

from sqlalchemy import exists, func, select

from ..config import get_settings
from ..db import sessionmaker, utcnow
from ..models import PersonalDeadline, Student
from . import linking

log = logging.getLogger("suchana.housekeeping")
TOUCH_EVERY = timedelta(hours=1)  # at most one database write per student per hour
LOOP_EVERY = 6 * 3600


def is_anonymous(student: Student) -> bool:
    return student.phone_hash.startswith("web-")


async def touch_student(s, student: Student) -> None:
    """Record that a website student used the app just now."""
    if not is_anonymous(student):
        return
    now = utcnow()
    if student.last_inbound_at is None or now - student.last_inbound_at > TOUCH_EVERY:
        student.last_inbound_at = now
        await s.commit()


async def purge_inactive_sessions(days: int | None = None, limit: int = 200) -> int:
    """Delete website-only sessions unused for `days` days, with everything that belongs to them.
    A session that still has a confirmed deadline to come (or one that passed within the last `days` days) is kept:
    deleting it would silently cancel a reminder the student asked for."""
    days = get_settings().stale_session_days if days is None else days
    if days <= 0:
        return 0
    cutoff = utcnow() - timedelta(days=days)
    async with sessionmaker()() as s:
        ids = (
            await s.scalars(
                select(Student.id)
                .where(
                    Student.phone_hash.like("web-%"),
                    func.coalesce(Student.last_inbound_at, Student.created_at) < cutoff,
                    ~exists().where(
                        PersonalDeadline.student_id == Student.id,
                        PersonalDeadline.status == "confirmed",
                        PersonalDeadline.due_date >= cutoff.date(),
                    ),
                )
                .limit(limit)
            )
        ).all()
    removed = 0
    for sid in ids:
        async with sessionmaker()() as s:
            st = await s.get(Student, sid)
            if st is not None:
                await linking.erase_student(s, st)
                removed += 1
    return removed


async def housekeeping_loop() -> None:
    while True:
        try:
            n = await purge_inactive_sessions()
            if n:
                log.info("deleted %d inactive anonymous sessions", n)
        except Exception:  # noqa: BLE001  (housekeeping must never stop the app)
            log.exception("housekeeping failed")
        await asyncio.sleep(LOOP_EVERY)
