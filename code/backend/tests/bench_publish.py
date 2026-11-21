"""Not part of the suite (file name does not start with test_). Run: pytest tests/bench_publish.py -s
Counts SQL statements and wall time to publish one notice to 300 students who each have a timetable."""

import time

from sqlalchemy import event, select

from app.db import get_engine, sessionmaker
from app.models import Job, Student, StudentSlot
from app.security import encrypt_phone, phone_hash

from .test_campus import publish
from .test_flow import join_student


async def test_bench_publish_300_students(client, admin, fakes):
    await join_student(client, admin)
    async with sessionmaker()() as s:
        first = await s.scalar(select(Student))
        for i in range(300):
            ph = f"9198{i:08d}"
            st = Student(phone_hash=phone_hash(ph), phone_enc=encrypt_phone(ph), college_id=first.college_id, preferred_lang="mr", consent_at=first.consent_at)
            s.add(st)
            await s.flush()
            s.add(StudentSlot(student_id=st.id, weekday=2, start="10:00", end="11:00", subject="Accounts", room="12"))
        await s.commit()
    n = {"sql": 0}
    event.listen(get_engine().sync_engine, "before_cursor_execute", lambda *a, **k: n.__setitem__("sql", n["sql"] + 1))
    t = time.perf_counter()
    await publish(client, admin)
    dt = time.perf_counter() - t
    async with sessionmaker()() as s:
        jobs = len((await s.scalars(select(Job))).all())
    print(f"\nBENCH publish+deliver 301 students: {n['sql']} SQL statements, {dt:.1f}s, {jobs} jobs, {len(fakes.tts_calls)} TTS calls, {len(fakes.sent)} messages")
