"""Database-backed actions for the student features, shared by the web API and the WhatsApp bot,
so both channels behave identically."""

from __future__ import annotations

import logging
import re
from datetime import datetime, time, timedelta

from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import utcnow
from ..errors import AppError
from ..models import (
    Notice,
    NoticeAction,
    NoticeStatus,
    PersonalDeadline,
    Reminder,
    Snap,
    Student,
    StudentSlot,
    TimetableRow,
)
from ..providers.base import ProviderError
from ..providers.llm import gemini_label, gemini_memory_answer
from ..providers.media import sniff_mime
from ..storage import get_storage, sha256_bytes
from .dates import WEEKDAYS, normalize
from .jobs import enqueue
from .matching import similarity
from .personal import (
    Slot,
    catch_readback,
    fallback_label,
    resolve_phrase,
    right_now,
    right_now_text,
    speak_time,
)

log = logging.getLogger("suchana.campus")


def local_now() -> datetime:
    from zoneinfo import ZoneInfo

    return datetime.now(ZoneInfo(get_settings().timezone))


# --------------------------------------------------------------------------- timetable


async def slots_for(s: AsyncSession, student_id: str) -> list[Slot]:
    rows = (await s.scalars(select(StudentSlot).where(StudentSlot.student_id == student_id))).all()
    return [Slot(r.weekday, r.start, r.end, r.subject, r.room) for r in rows]


async def replace_slots(s: AsyncSession, student_id: str, slots: list[dict], source_notice_id: str | None = None) -> int:
    await s.execute(delete(StudentSlot).where(StudentSlot.student_id == student_id))
    for sl in slots:
        s.add(StudentSlot(student_id=student_id, weekday=sl["weekday"], start=sl["start"], end=sl["end"], subject=sl["subject"], room=sl.get("room"), source_notice_id=source_notice_id))
    await s.commit()
    return len(slots)


def _weekday_of(day: str | None) -> int | None:
    return WEEKDAYS.get(normalize(day or "").strip()) if day else None


def timetable_batches(rows: list[TimetableRow]) -> list[str]:
    return sorted({r.batch.strip() for r in rows if r.batch and r.batch.strip()})


async def timetable_notices(s: AsyncSession, college_id: str) -> list[Notice]:
    from sqlalchemy.orm import selectinload

    notices = (
        await s.scalars(
            select(Notice)
            .where(Notice.college_id == college_id, Notice.status == NoticeStatus.published, Notice.category == "timetable")
            .options(selectinload(Notice.timetable))
            .order_by(Notice.published_at.desc())
        )
    ).all()
    return [n for n in notices if any(_weekday_of(r.day) is not None and r.start and r.end for r in n.timetable)]


def slots_from_rows(rows: list[TimetableRow], batch: str | None) -> list[dict]:
    out = []
    for r in rows:
        wd = _weekday_of(r.day)
        if wd is None or not (r.start and r.end and r.subject) or r.on_date:
            continue
        if r.batch and batch and r.batch.strip().lower() != batch.strip().lower():
            continue
        if r.batch and not batch:
            continue
        out.append({"weekday": wd, "start": r.start, "end": r.end, "subject": r.subject, "room": r.room})
    return out


async def import_timetable(s: AsyncSession, student: Student, batch: str | None) -> tuple[int, list[str]]:
    """Fill the student's own timetable from their college's newest published timetable notice."""
    if not student.college_id:
        raise AppError("Join your college first.", code="no_college")
    notices = await timetable_notices(s, student.college_id)
    if not notices:
        raise AppError("Your college has not published a timetable yet.", code="no_timetable", status_code=404)
    notice = notices[0]
    batches = timetable_batches(notice.timetable)
    slots = slots_from_rows(notice.timetable, batch)
    if not slots:
        if batches:
            raise AppError(f"Which batch are you in? Choose one of: {', '.join(batches)}.", code="batch_required", detail=batches)
        raise AppError("No classes found in the timetable.", code="no_timetable", status_code=404)
    n = await replace_slots(s, student.id, slots, notice.id)
    return n, batches


# --------------------------------------------------------------------------- right now


async def now_summary(s: AsyncSession, student: Student) -> dict:
    slots = await slots_for(s, student.id)
    now = local_now()
    today = now.date()
    due_today = [
        f"{a.label}" + (f" at {speak_time(a.due_time)}" if a.due_time else "")
        for a in (
            await s.scalars(
                select(NoticeAction)
                .join(Notice, Notice.id == NoticeAction.notice_id)
                .where(Notice.college_id == student.college_id, Notice.status == NoticeStatus.published, NoticeAction.due_date == today)
            )
        ).all()
    ] if student.college_id else []
    due_today += [
        p.label for p in (await s.scalars(select(PersonalDeadline).where(PersonalDeadline.student_id == student.id, PersonalDeadline.status == "confirmed", PersonalDeadline.due_date == today))).all()
    ]
    r = right_now(slots, now)

    def slot_json(sl: Slot | None) -> dict | None:
        return {"subject": sl.subject, "start": sl.start, "end": sl.end, "room": sl.room} if sl else None

    return {
        "text": right_now_text(slots, now, due_today),
        "current": slot_json(r["current"]),
        "next": slot_json(r["next"]),
        "due_today": due_today,
        "has_timetable": bool(slots),
        "at": now.isoformat(),
    }


# --------------------------------------------------------------------------- verbal deadline catcher


async def make_label(transcript: str) -> str:
    try:
        label = (await gemini_label(transcript)).strip(" .")
        if 2 <= len(label) <= 150:
            return label
    except ProviderError:
        pass
    return fallback_label(transcript)


async def create_catch(s: AsyncSession, student: Student, transcript: str, source: str) -> tuple[PersonalDeadline, str]:
    transcript = re.sub(r"\s+", " ", transcript).strip()
    if len(transcript) < 3:
        raise AppError("I did not catch that. Please try again.", code="empty_speech")
    resolved = resolve_phrase(transcript, local_now().date())
    label = await make_label(transcript)
    pd = PersonalDeadline(
        student_id=student.id,
        label=label[:300],
        due_date=resolved.due,
        due_time=resolved.time,
        transcript=transcript[:1000],
        source=source,
        status="pending",
        needs_date=resolved.due is None,
    )
    s.add(pd)
    student.catch_until = None
    await s.commit()
    return pd, catch_readback(label, resolved)


async def latest_pending(s: AsyncSession, student_id: str) -> PersonalDeadline | None:
    return await s.scalar(
        select(PersonalDeadline)
        .where(PersonalDeadline.student_id == student_id, PersonalDeadline.status == "pending", PersonalDeadline.created_at >= utcnow() - timedelta(minutes=20))
        .order_by(PersonalDeadline.created_at.desc())
        .limit(1)
    )


async def fix_date(s: AsyncSession, pd: PersonalDeadline, transcript: str) -> str:
    """The student said the date again: re-resolve it, keep the original label."""
    resolved = resolve_phrase(transcript, local_now().date())
    if resolved.due is not None:
        pd.due_date, pd.needs_date = resolved.due, False
    if resolved.time:
        pd.due_time = resolved.time
    await s.commit()
    return catch_readback(pd.label, resolved if resolved.due else resolve_phrase(pd.transcript or "", local_now().date()))


async def confirm_catch(s: AsyncSession, student: Student, pd: PersonalDeadline) -> list[Reminder]:
    if pd.status == "confirmed":
        return []
    if pd.due_date is None:
        raise AppError("Tell me the date first, for example 'next Friday' or '28 November'.", code="date_required")
    if pd.due_date < local_now().date():
        raise AppError("That date has already passed. Please say the date again.", code="date_past")
    cfg = get_settings()
    pd.status, pd.confirmed_at = "confirmed", utcnow()
    now = utcnow()
    made: list[Reminder] = []
    tzinfo = local_now().tzinfo
    for off in cfg.reminder_offsets:
        send = datetime.combine(pd.due_date - timedelta(days=off), time(cfg.reminder_hour_local), tzinfo=tzinfo).astimezone(now.tzinfo)
        if send <= now:
            continue
        r = Reminder(kind="personal", personal_id=pd.id, student_id=student.id, offset_days=off, send_at=send)
        s.add(r)
        made.append(r)
    await s.commit()
    return made


def confirmation_text(pd: PersonalDeadline, reminders: int) -> str:
    from .dates import speak_date

    when = speak_date(pd.due_date) + (f" at {speak_time(pd.due_time)}" if pd.due_time else "")
    tail = f" I will remind you {reminders} time{'s' if reminders != 1 else ''} before." if reminders else " It is very soon, so I will not send more reminders."
    return f"Saved: {pd.label}, due {when}.{tail}"


# --------------------------------------------------------------------------- lecture memory


async def create_snap(s: AsyncSession, student: Student, data: bytes) -> Snap:
    """Label the photo with the class the student is in right now, then read it in the background."""
    slots = await slots_for(s, student.id)
    current = right_now(slots, local_now())["current"] if slots else None
    mime = sniff_mime(data)
    ext = {"image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    key = await get_storage().put(f"snaps/{student.id}/{sha256_bytes(data)}{ext}", data)
    sn = Snap(student_id=student.id, subject=current.subject if current else None, room=current.room if current else None, source_key=key)
    s.add(sn)
    await s.flush()
    await enqueue(s, "process_snap", {"snap_id": sn.id}, dedupe_key=f"snap:{sn.id}", max_attempts=3)
    await s.commit()
    return sn


def snap_score(query: str, text: str) -> float:
    q_tokens = [t for t in re.split(r"\W+", normalize(query)) if len(t) > 2]
    t_norm = normalize(text)
    overlap = sum(1 for t in q_tokens if t in t_norm) / len(q_tokens) if q_tokens else 0.0
    return round(0.65 * overlap + 0.35 * similarity(query, text), 3)


async def search_snaps(s: AsyncSession, student_id: str, query: str, limit: int = 3) -> list[tuple[Snap, float]]:
    snaps = (await s.scalars(select(Snap).where(Snap.student_id == student_id, Snap.status == "ready").order_by(Snap.captured_at.desc()).limit(200))).all()
    scored = [(sn, snap_score(query + " " + (sn.subject or ""), (sn.ocr_text or "") + " " + (sn.subject or ""))) for sn in snaps]
    scored = [(sn, sc) for sn, sc in scored if sc >= 0.4]
    return sorted(scored, key=lambda x: x[1], reverse=True)[:limit]


def snap_context(snaps: list[Snap]) -> str:
    return "\n\n".join(f"[{sn.subject or 'Unknown subject'}, {sn.captured_at.astimezone(local_now().tzinfo):%A %d %B}] {(sn.ocr_text or '')[:1200]}" for sn in snaps)


async def answer_from_memory(s: AsyncSession, student: Student, question: str) -> dict | None:
    """Answer from the student's own saved boards. None if nothing relevant is saved."""
    hits = await search_snaps(s, student.id, question, limit=2)
    if not hits:
        return None
    snaps = [h[0] for h in hits]
    try:
        text = await gemini_memory_answer(question, snap_context(snaps), student.preferred_lang or "en")
    except ProviderError:
        best = snaps[0]
        text = f"From your {best.subject or 'saved'} board on {best.captured_at.astimezone(local_now().tzinfo):%A %d %B}: {(best.summary or best.ocr_text or '')[:400]}"
    return {"text": text, "snap_ids": [sn.id for sn in snaps], "scores": [sc for _, sc in hits]}
