"""Job handlers: the notice lifecycle.

  upload ─► process_notice ─► needs_review ─(human approves)─► publish_notice ─► deliver × students
                                                                   └─► reminders (scheduled)
  WhatsApp in ─► handle_inbound ─► commands | question answering | forward check

Every handler is idempotent: running it twice (after a crash, or a retried job)
never sends a student the same message twice or pays for OCR twice.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Awaitable, Callable
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import sessionmaker, utcnow
from ..errors import PermanentError, PipelineError
from ..events import bus
from ..models import (
    AuditEvent,
    College,
    Delivery,
    DeliveryStatus,
    ForwardCheck,
    Job,
    Notice,
    NoticeAction,
    NoticeSource,
    NoticeStatus,
    OutboundMessage,
    PersonalDeadline,
    PushSubscription,
    Reminder,
    Rendition,
    Route,
    StaffUser,
    Student,
    StudentSlot,
    TimetableRow,
    Verification,
)
from ..providers import whatsapp
from ..providers.base import ProviderError
from ..providers.llm import NoticeExtraction, gemini_extract
from ..providers.media import prepare_document
from ..providers.ocr import run_ocr
from ..providers.tts import synthesize
from ..security import decrypt_phone, sign_student_token
from ..storage import get_storage
from .dates import (
    diff_actions,
    first_title_line,
    rule_based_actions,
    speak_date,
    verify_action,
    verify_timetable_times,
)
from .jobs import enqueue
from .messaging import notify_student, push_to_student
from .notify import compose_notice_message, compose_reminder, notice_link
from .personal import (
    DEFAULT_MINUTES,
    Slot,
    changed_class_text,
    clash_text,
    exam_eve_text,
    find_clashes,
    place_matches,
    speak_time,
    subject_matches,
)

log = logging.getLogger("suchana.steps")

Handler = Callable[[Job], Awaitable[None]]
HANDLERS: dict[str, Handler] = {}


def handler(name: str):
    def deco(fn: Handler) -> Handler:
        HANDLERS[name] = fn
        return fn

    return deco


def tz() -> ZoneInfo:
    return ZoneInfo(get_settings().timezone)


def today_local() -> date:
    return datetime.now(tz()).date()


def audit(s: AsyncSession, event: str, *, notice: Notice | None = None, college_id: str | None = None, actor: str = "system", **detail) -> None:
    s.add(
        AuditEvent(
            event=event,
            actor=actor,
            notice_id=notice.id if notice else None,
            college_id=college_id or (notice.college_id if notice else None),
            detail=detail or None,
        )
    )


def emit_notice(notice: Notice) -> None:
    bus.publish(
        "notice.updated",
        {"id": notice.id, "public_id": notice.public_id, "status": notice.status, "stage": notice.stage, "title": notice.title},
        college_id=notice.college_id,
    )


async def load_notice(s: AsyncSession, notice_id: str) -> Notice | None:
    return await s.scalar(
        select(Notice)
        .where(Notice.id == notice_id)
        .options(
            selectinload(Notice.actions),
            selectinload(Notice.timetable),
            selectinload(Notice.renditions),
            selectinload(Notice.approved_by),
            selectinload(Notice.college),
        )
    )


async def set_stage(s: AsyncSession, notice: Notice, stage: str | None, status: str | None = None) -> None:
    notice.stage = stage
    if status:
        notice.status = status
    await s.commit()
    emit_notice(notice)


# ---------------------------------------------------------------------------------------
# 1. Understand a notice
# ---------------------------------------------------------------------------------------


def _iso_or_none(value: str | None) -> date | None:
    try:
        return date.fromisoformat(value) if value else None
    except ValueError:
        return None


def _fallback_extraction(ocr_text: str, ref: date) -> dict:
    """Used when the LLM is unavailable: keep moving, flag everything for a human."""
    title = first_title_line(ocr_text)
    actions = rule_based_actions(ocr_text, ref)
    spoken = f"{title}. " + " ".join(f"Date mentioned: {a['due_date']}." for a in actions[:4])
    return {
        "is_notice": True,
        "title": title,
        "issuer": None,
        "category": "general",
        "issue_date": None,
        "audience": None,
        "language": "mixed",
        "legibility": "partial",
        "actions": actions,
        "timetable": [],
        "summaries": {"en": spoken[:450]},
        "body_markdown": ocr_text,
        "reviewer_notes": ["AI understanding was unavailable; dates were found by pattern matching. Please check every field."],
    }


def _hhmm(value: str | None) -> str | None:
    """Accept '10:30', '10.30', '10:30 AM', '2 pm'; return 'HH:MM' or None."""
    if not value:
        return None
    m = re.match(r"^\s*(\d{1,2})(?:[:.](\d{2}))?\s*([ap]\.?m\.?)?\s*$", value.strip().lower())
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3):
        if m.group(3).startswith("p") and h < 12:
            h += 12
        if m.group(3).startswith("a") and h == 12:
            h = 0
    return f"{h:02d}:{mi:02d}" if h < 24 and mi < 60 else None


def apply_extraction(notice: Notice, ext: dict, ref: date) -> None:
    """Turn raw extraction into verified, editable rows. Safe to call again on retry."""
    ocr = notice.ocr_text or ""
    notice.title = (ext.get("title") or first_title_line(ocr))[:300]
    notice.issuer = (ext.get("issuer") or None) and ext["issuer"][:200]
    notice.category = ext.get("category") or "general"
    notice.issue_date = _iso_or_none(ext.get("issue_date"))
    notice.audience = (ext.get("audience") or None) and ext["audience"][:200]
    notice.body_markdown = ext.get("body_markdown") or ocr
    notice.summaries = {k: v for k, v in (ext.get("summaries") or {}).items() if v}

    notice.actions.clear()
    flags: list[str] = list(ext.get("reviewer_notes") or [])
    verified = 0
    dated = 0
    for i, a in enumerate(ext.get("actions") or []):
        a = {**a, "due_time": _hhmm(a.get("due_time"))}
        res = verify_action(
            due_date=a.get("due_date"),
            due_time=a.get("due_time"),
            amount=a.get("amount"),
            source_quote=a.get("source_quote"),
            ocr_text=ocr,
            ref=ref,
        )
        if a.get("due_date"):
            dated += 1
            verified += res.status == Verification.verified
        notice.actions.append(
            NoticeAction(
                sort=i,
                kind=a.get("kind") or "other",
                label=(a.get("label") or "Action")[:300],
                due_date=_iso_or_none(a.get("due_date")),
                due_time=a.get("due_time"),
                amount=a.get("amount"),
                venue=a.get("venue"),
                source_quote=a.get("source_quote"),
                verification=res.status,
                verify_reasons=res.reasons or None,
            )
        )

    notice.timetable.clear()
    rows = ext.get("timetable") or []
    for i, r in enumerate(rows):
        notice.timetable.append(
            TimetableRow(
                sort=i,
                day=r.get("day"),
                on_date=_iso_or_none(r.get("date")),
                start=_hhmm(r.get("start")),
                end=_hhmm(r.get("end")),
                subject=r.get("subject"),
                room=r.get("room"),
                faculty=r.get("faculty"),
                batch=r.get("batch"),
            )
        )
    flags += verify_timetable_times(rows, ocr)

    legibility = ext.get("legibility", "good")
    if legibility != "good":
        flags.append(f"Scan legibility is {legibility}; compare with the original image.")
    if notice.ocr_engine and notice.ocr_engine != "sarvam-vision":
        flags.append(f"Text was read by the backup reader ({notice.ocr_engine}).")
    if ext.get("is_notice") is False:
        flags.append("This may not be an official notice.")

    score = (verified / dated) if dated else 1.0
    score *= {"good": 1.0, "partial": 0.8, "poor": 0.5}.get(legibility, 0.8)
    if notice.extraction_engine == "rules":
        score *= 0.5
    notice.confidence = round(score, 2)
    notice.flags = flags or None


@handler("process_notice")
async def process_notice(job: Job) -> None:
    s_ = get_settings()
    async with sessionmaker()() as s:
        notice = await load_notice(s, job.payload["notice_id"])
        if notice is None:
            raise PermanentError("notice no longer exists")
        if notice.status not in (NoticeStatus.queued, NoticeStatus.processing, NoticeStatus.failed):
            return  # already processed (duplicate job)
        notice.error_code = notice.error_message = None
        await set_stage(s, notice, "reading", NoticeStatus.processing)

        raw = await get_storage().get(notice.source_file_key)
        doc = prepare_document(raw, max_pages=s_.max_pdf_pages, max_side=s_.max_image_side_px)
        notice.page_count = doc.pages

        if not notice.ocr_text:
            result = await run_ocr(doc, notice.language_hint or "mr")
            notice.ocr_text, notice.ocr_engine = result.text, result.engine
            audit(s, "ocr.done", notice=notice, engine=result.engine, cached=result.cached, pages=result.pages)
        await set_stage(s, notice, "understanding")

        ref = today_local()
        if not notice.extraction:
            try:
                ext: NoticeExtraction = await gemini_extract(
                    notice.ocr_text, image=doc.data, image_mime=doc.mime, today=ref.isoformat()
                )
                notice.extraction, notice.extraction_engine = ext.model_dump(), f"gemini:{s_.gemini_model}"
            except ProviderError as exc:
                # Retry the job for transient errors on early attempts; afterwards degrade gracefully.
                if exc.retryable and job.attempts < 2:
                    raise
                log.warning("extraction fell back to rules: %s", exc)
                notice.extraction, notice.extraction_engine = _fallback_extraction(notice.ocr_text, ref), "rules"
            await s.commit()
        await set_stage(s, notice, "checking_dates")

        apply_extraction(notice, notice.extraction, ref)
        if notice.supersedes_id:
            old = await load_notice(s, notice.supersedes_id)
            if old:
                notice.changes_from_previous = diff_actions(_actions_as_dicts(old.actions), _actions_as_dicts(notice.actions)) or None
        notice.status = NoticeStatus.needs_review
        notice.stage = None
        audit(s, "notice.ready_for_review", notice=notice, confidence=notice.confidence)
        await s.commit()
        emit_notice(notice)

        if notice.source == NoticeSource.forward:
            await enqueue(s, "reply_forward", {"notice_id": notice.id}, dedupe_key=f"reply_forward:{notice.id}", notice_id=notice.id)
            await s.commit()


def _actions_as_dicts(actions: list[NoticeAction]) -> list[dict]:
    return [
        {
            "kind": a.kind,
            "label": a.label,
            "due_date": a.due_date.isoformat() if a.due_date else None,
            "due_time": a.due_time,
            "venue": a.venue,
            "amount": a.amount,
        }
        for a in actions
    ]


# ---------------------------------------------------------------------------------------
# 2. Publish an approved notice
# ---------------------------------------------------------------------------------------


def spoken_text(notice: Notice, lang: str) -> str | None:
    summaries = notice.summaries or {}
    if summaries.get(lang):
        return summaries[lang]
    if lang == "en":
        return f"{notice.title}. " + " ".join(f"{a.label}." for a in notice.actions[:4])
    return None


async def target_students(s: AsyncSession, notice: Notice) -> list[Student]:
    students = (
        await s.scalars(select(Student).where(Student.college_id == notice.college_id, Student.opted_out_at.is_(None)))
    ).all()
    tags = set(notice.audience_tags or [])
    if not tags:
        return list(students)
    return [st for st in students if not st.audience_tags or tags & set(st.audience_tags)]


async def make_renditions(s: AsyncSession, notice: Notice, langs: list[str], *, allow_retry: bool) -> None:
    existing = {r.lang: r for r in notice.renditions}
    for lang in langs:
        text = spoken_text(notice, lang)
        if not text:
            continue
        r = existing.get(lang)
        if r and r.audio_key and r.text == text:
            continue
        try:
            audio = await synthesize(text, lang)
        except ProviderError as exc:
            if exc.retryable and allow_retry:
                raise
            log.warning("TTS failed for %s/%s: %s", notice.id, lang, exc)
            audio = None
            err = str(exc)
        if r is None:
            r = Rendition(notice_id=notice.id, lang=lang, text=text)
            notice.renditions.append(r)
            existing[lang] = r
        r.text = text
        r.audio_key = audio.audio_key if audio else None
        r.voice_note_key = audio.voice_note_key if audio else None
        r.tts_engine = audio.engine if audio else None
        r.error = None if audio else err
        await s.commit()
        await set_stage(s, notice, f"voice_{lang}")


@handler("publish_notice")
async def publish_notice(job: Job) -> None:
    s_ = get_settings()
    async with sessionmaker()() as s:
        notice = await load_notice(s, job.payload["notice_id"])
        if notice is None or notice.status != NoticeStatus.approved:
            return
        students = await target_students(s, notice)
        base_langs = [x.strip() for x in s_.publish_langs.split(",") if x.strip()]
        langs = list(dict.fromkeys(base_langs + [st.preferred_lang for st in students]))
        await make_renditions(s, notice, langs, allow_retry=job.attempts < job.max_attempts - 1)

        notice.status = NoticeStatus.published
        notice.stage = None
        notice.published_at = utcnow()
        previously_reached: set[str] = set()
        if notice.supersedes_id:
            old = await s.get(Notice, notice.supersedes_id)
            if old and old.status == NoticeStatus.published:
                old.status = NoticeStatus.superseded
                old.superseded_at = utcnow()
                emit_notice(old)
            await s.execute(
                update(Reminder).where(Reminder.notice_id == notice.supersedes_id, Reminder.status == "pending").values(status="cancelled")
            )
            previously_reached = set(
                (await s.scalars(select(Delivery.student_id).where(Delivery.notice_id == notice.supersedes_id))).all()
            )
        audit(s, "notice.published", notice=notice, students=len(students), langs=langs)

        # Fan out deliveries (idempotent via dedupe keys). One query for what already exists, not one per student.
        done_keys = set((await s.scalars(select(Delivery.dedupe_key).where(Delivery.notice_id == notice.id))).all())
        for st in students:
            kind = "update" if st.id in previously_reached else "notice"
            key = f"{notice.id}:{st.id}:{kind}"
            if key in done_keys:
                continue
            d = Delivery(dedupe_key=key, notice_id=notice.id, student_id=st.id, kind=kind)
            s.add(d)
            await s.flush()
            await enqueue(s, "deliver", {"delivery_id": d.id}, dedupe_key=f"deliver:{d.id}", notice_id=notice.id)

        # Schedule reminders for dates a human or the checker trusts.
        offsets = s_.reminder_offsets
        now = utcnow()
        have_reminders = set(
            (await s.execute(select(Reminder.action_id, Reminder.student_id, Reminder.offset_days, Reminder.kind).where(Reminder.notice_id == notice.id))).all()
        )
        for a in notice.actions:
            if not a.due_date or a.verification == Verification.needs_check:
                continue
            for st in students:
                if not st.reminders_enabled:
                    continue
                for off in offsets:
                    send_local = datetime.combine(a.due_date - timedelta(days=off), time(s_.reminder_hour_local), tzinfo=tz())
                    if off == 0 and a.due_time:
                        hh, mm = map(int, a.due_time.split(":"))
                        due_local = datetime.combine(a.due_date, time(hh, mm), tzinfo=tz())
                        send_local = min(send_local, due_local - timedelta(hours=2))
                    send_at = send_local.astimezone(now.tzinfo)
                    if send_at <= now:
                        continue
                    if (a.id, st.id, off, "deadline") not in have_reminders:
                        s.add(Reminder(action_id=a.id, notice_id=notice.id, student_id=st.id, offset_days=off, send_at=send_at))
        # Exam Eve Pack: a briefing the evening before each exam.
        for a in notice.actions:
            if a.kind != "exam" or not a.due_date or a.verification == Verification.needs_check:
                continue
            send_local = datetime.combine(a.due_date - timedelta(days=1), time(s_.exam_eve_hour_local), tzinfo=tz())
            send_at = send_local.astimezone(now.tzinfo)
            if send_at <= now:
                continue
            for st in students:
                if not st.reminders_enabled:
                    continue
                if (a.id, st.id, -1, "exam_eve") not in have_reminders:
                    s.add(Reminder(kind="exam_eve", action_id=a.id, notice_id=notice.id, student_id=st.id, offset_days=-1, send_at=send_at))
        await enqueue(s, "personalize_notice", {"notice_id": notice.id}, dedupe_key=f"personalize:{notice.id}", notice_id=notice.id)
        await s.commit()
        emit_notice(notice)
        bus.publish("metrics.changed", {}, college_id=notice.college_id)


# ---------------------------------------------------------------------------------------
# 3. Deliver to one student
# ---------------------------------------------------------------------------------------


def in_service_window(student: Student) -> bool:
    return bool(student.last_inbound_at and utcnow() - student.last_inbound_at < timedelta(hours=23, minutes=30))


def approver_label(notice: Notice) -> str | None:
    if not notice.approved_by:
        return None
    return notice.approved_by.office or notice.approved_by.name


async def send_voice(phone: str, rendition: Rendition | None) -> list[str]:
    if not rendition or not (rendition.voice_note_key or rendition.audio_key):
        return []
    key = rendition.voice_note_key or rendition.audio_key
    mime = "audio/ogg" if key.endswith(".ogg") else "audio/mpeg"
    data = await get_storage().get(key)
    media_id = await whatsapp.upload_media(data, mime, key.rsplit("/", 1)[-1])
    return [(await whatsapp.send_audio(phone, media_id)).message_id]


@handler("deliver")
async def deliver(job: Job) -> None:
    s_ = get_settings()
    async with sessionmaker()() as s:
        d = await s.get(Delivery, job.payload["delivery_id"])
        if d is None or d.status in (DeliveryStatus.sent, DeliveryStatus.delivered, DeliveryStatus.read, DeliveryStatus.skipped):
            return
        student = await s.get(Student, d.student_id)
        notice = await load_notice(s, d.notice_id)
        if student is None or notice is None:
            return
        if student.opted_out_at:
            d.status = DeliveryStatus.skipped
            await s.commit()
            return
        # Buzz Codes: a phone vibration in its own job, once per delivery, so a push failure never blocks WhatsApp.
        if await s.scalar(select(PushSubscription.id).where(PushSubscription.student_id == student.id).limit(1)):
            await enqueue(s, "push_delivery", {"delivery_id": d.id}, dedupe_key=f"push:{d.id}", max_attempts=2)
            await s.commit()
        phone = decrypt_phone(student.phone_enc)
        lang = student.preferred_lang or "en"
        token = sign_student_token(student.id)
        college_name = notice.college.name if notice.college else "your college"
        ids: list[str] = list(d.wa_message_ids or [])

        try:
            if not in_service_window(student):
                raise whatsapp.WindowClosed()
            text = compose_notice_message(notice, college_name, lang, approver_label(notice), token)
            if not ids:  # voice note first: it is what a blind student wants to hear
                rendition = next((r for r in notice.renditions if r.lang == lang and r.audio_key), None) or next(
                    (r for r in notice.renditions if r.lang == "en" and r.audio_key), None
                )
                try:
                    ids += await send_voice(phone, rendition)
                except ProviderError as exc:
                    if isinstance(exc, whatsapp.WindowClosed):
                        raise
                    log.warning("voice note failed, sending text only: %s", exc)
                d.wa_message_ids = ids or ["text-only"]
                await s.commit()  # checkpoint: a retry will not resend the voice note
            ids.append((await whatsapp.send_text(phone, text)).message_id)
        except whatsapp.WindowClosed:
            if s_.whatsapp_notice_template:
                first = next((a for a in notice.actions if a.due_date), None)
                params = [
                    notice.title or "Notice",
                    f"{first.label}: {first.due_date.isoformat()}" if first else "See the notice",
                    notice_link(notice, token),
                ]
                ids.append((await whatsapp.send_template(phone, s_.whatsapp_notice_template, s_.whatsapp_template_lang, params)).message_id)
            else:
                d.status = DeliveryStatus.deferred_window
                d.error = "Student has not messaged in 24h and no template is configured; will send when they next message."
                await s.commit()
                return
        d.wa_message_ids = [i for i in ids if i != "text-only"]
        for mid in d.wa_message_ids:
            if not await s.get(OutboundMessage, mid):
                s.add(OutboundMessage(wa_message_id=mid, delivery_id=d.id))
        d.status = DeliveryStatus.sent
        d.error = None
        audit(s, "delivery.sent", notice=notice, student_id=student.id, kind=d.kind)
        await s.commit()
        bus.publish("delivery.updated", {"notice_id": notice.id, "status": d.status}, college_id=notice.college_id)


@handler("push_delivery")
async def push_delivery(job: Job) -> None:
    async with sessionmaker()() as s:
        d = await s.get(Delivery, job.payload["delivery_id"])
        if d is None:
            return
        student = await s.get(Student, d.student_id)
        notice = await load_notice(s, d.notice_id)
        if student is None or notice is None or student.opted_out_at:
            return
        kind = "update" if d.kind == "update" else ("exam" if any(a.kind == "exam" for a in notice.actions) else "notice")
        await push_to_student(s, student, kind=kind, title=notice.title or "New notice", body=(notice.summaries or {}).get(student.preferred_lang or "en") or "Open to listen.", url=notice_link(notice, sign_student_token(student.id)))


# ---------------------------------------------------------------------------------------
# 4. Reminders
# ---------------------------------------------------------------------------------------


@handler("send_reminder")
async def send_reminder(job: Job) -> None:
    s_ = get_settings()
    async with sessionmaker()() as s:
        r = await s.get(Reminder, job.payload["reminder_id"])
        if r is None or r.status not in ("pending", "enqueued"):
            return
        if r.kind == "exam_eve":
            await send_exam_eve(s, r)
            return
        if r.kind == "personal":
            await send_personal_reminder(s, r)
            return
        action = await s.get(NoticeAction, r.action_id)
        notice = await load_notice(s, r.notice_id)
        student = await s.get(Student, r.student_id)
        if not action or not notice or not student or notice.status != NoticeStatus.published or student.opted_out_at or not student.reminders_enabled:
            r.status = "cancelled"
            await s.commit()
            return
        days_left = (action.due_date - today_local()).days if action.due_date else 0
        phone = decrypt_phone(student.phone_enc)
        token = sign_student_token(student.id)
        try:
            if not in_service_window(student):
                raise whatsapp.WindowClosed()
            await whatsapp.send_text(phone, compose_reminder(action, notice, days_left, student.preferred_lang, token))
        except whatsapp.WindowClosed:
            if not s_.whatsapp_notice_template:
                r.status = "failed"
                await s.commit()
                return
            await whatsapp.send_template(
                phone,
                s_.whatsapp_notice_template,
                s_.whatsapp_template_lang,
                [f"Reminder: {notice.title}", f"{action.label}: {action.due_date}", notice_link(notice, token)],
            )
        r.status = "sent"
        audit(s, "reminder.sent", notice=notice, student_id=student.id, offset_days=r.offset_days)
        await s.commit()


async def enqueue_due_reminders() -> int:
    """Called by the scheduler loop."""
    async with sessionmaker()() as s:
        due = (await s.scalars(select(Reminder).where(Reminder.status == "pending", Reminder.send_at <= utcnow()).limit(200))).all()
        for r in due:
            r.status = "enqueued"
            await enqueue(s, "send_reminder", {"reminder_id": r.id}, dedupe_key=f"reminder:{r.id}", notice_id=r.notice_id)
        await s.commit()
        return len(due)


# ---------------------------------------------------------------------------------------
# 5. Forward checks ("is this photo the real, latest notice?")
# ---------------------------------------------------------------------------------------


async def latest_in_series(s: AsyncSession, notice: Notice) -> Notice:
    latest = await s.scalar(
        select(Notice)
        .where(Notice.series_id == notice.series_id, Notice.status == NoticeStatus.published)
        .order_by(Notice.version.desc())
        .limit(1)
    )
    return latest or notice


async def match_text(s: AsyncSession, text: str, college_id: str | None) -> tuple[str, float, Notice | None, Notice | None]:
    """Return (result, similarity, matched, latest)."""
    from .matching import MATCH_THRESHOLD, similarity

    q = select(Notice).where(
        Notice.source == NoticeSource.desk,
        Notice.status.in_([NoticeStatus.published, NoticeStatus.superseded]),
        Notice.ocr_text.is_not(None),
    )
    if college_id:
        q = q.where(Notice.college_id == college_id)
    best, best_score = None, 0.0
    for n in (await s.scalars(q.order_by(Notice.published_at.desc()).limit(500))).all():
        score = similarity(text, n.ocr_text or "")
        if score > best_score:
            best, best_score = n, score
    if best is None or best_score < MATCH_THRESHOLD:
        return "unknown", best_score, None, None
    if best.status == NoticeStatus.superseded:
        latest = await latest_in_series(s, best)
        return "superseded", best_score, best, latest
    return "official", best_score, best, best


@handler("check_forward_web")
async def check_forward_web(job: Job) -> None:
    s_ = get_settings()
    async with sessionmaker()() as s:
        fc = await s.get(ForwardCheck, job.payload["check_id"])
        if fc is None or fc.result != "pending":
            return
        raw = await get_storage().get(job.payload["file_key"])
        doc = prepare_document(raw, max_pages=s_.max_pdf_pages, max_side=s_.max_image_side_px)
        ocr = await run_ocr(doc, "mr")
        result, score, matched, latest = await match_text(s, ocr.text, fc.college_id)
        fc.result, fc.similarity = result, score
        fc.matched_notice_id = matched.id if matched else None
        fc.latest_notice_id = latest.id if latest else None
        audit(s, "forward.checked", college_id=fc.college_id, result=result, channel="web")
        await s.commit()
        await get_storage().delete(job.payload["file_key"])  # do not keep strangers' uploads
        bus.publish("check.done", {"id": fc.id, "result": result}, college_id=fc.college_id)


@handler("reply_forward")
async def reply_forward(job: Job) -> None:
    """Reply to a student whose forwarded photo matched nothing official: give them an unverified reading."""
    async with sessionmaker()() as s:
        notice = await load_notice(s, job.payload["notice_id"])
        if notice is None or not notice.forwarded_by_student_id:
            return
        student = await s.get(Student, notice.forwarded_by_student_id)
        if student is None:
            return
        lang = student.preferred_lang or "en"
        await make_renditions(s, notice, [lang], allow_retry=False)
        phone = decrypt_phone(student.phone_enc)
        rendition = next((r for r in notice.renditions if r.lang == lang and r.audio_key), None)
        lines = [
            "This notice is NOT in your college's official list, so it has not been checked by the college. Here is what it says:",
            "",
            notice.title or "",
        ]
        for a in notice.actions[:5]:
            if a.due_date:
                lines.append(f"- {a.label}: {a.due_date.strftime('%A, %d %B %Y')}" + (" (please confirm)" if a.verification == Verification.needs_check else ""))
        lines += ["", "A volunteer will double-check it. Reply HELP for options."]
        try:
            await send_voice(phone, rendition)
        except ProviderError as exc:
            log.warning("voice for forward reply failed: %s", exc)
        await whatsapp.send_text(phone, "\n".join(lines))
        audit(s, "forward.unofficial_reply", notice=notice, student_id=student.id)
        await s.commit()


# ---------------------------------------------------------------------------------------
# 5b. Samyukta: Clash Radar, Exam Eve Pack, personal reminders
# ---------------------------------------------------------------------------------------


async def student_slots(s: AsyncSession, student_id: str) -> list[Slot]:
    rows = (await s.scalars(select(StudentSlot).where(StudentSlot.student_id == student_id))).all()
    return [Slot(r.weekday, r.start, r.end, r.subject, r.room) for r in rows]


def event_window(a: NoticeAction) -> tuple[date, str, int] | None:
    if not a.due_date or not a.due_time or a.kind not in DEFAULT_MINUTES:
        return None
    return a.due_date, a.due_time, DEFAULT_MINUTES[a.kind]


@handler("personalize_notice")
async def personalize_notice(job: Job) -> None:
    """Tell each student only what a new notice means for THEM: clashes and changes to their own classes."""
    async with sessionmaker()() as s:
        notice = await load_notice(s, job.payload["notice_id"])
        if notice is None or notice.status != NoticeStatus.published:
            return
        link = notice_link(notice)
        students = await target_students(s, notice)
        by_student: dict[str, list[Slot]] = {}
        for r in (await s.scalars(select(StudentSlot).where(StudentSlot.student_id.in_([x.id for x in students])))).all() if students else []:
            by_student.setdefault(r.student_id, []).append(Slot(r.weekday, r.start, r.end, r.subject, r.room))
        claimed = set((await s.scalars(select(Delivery.dedupe_key).where(Delivery.notice_id == notice.id, Delivery.kind == "clash"))).all())
        for st in students:
            slots = by_student.get(st.id)
            if not slots:
                continue
            lang = st.preferred_lang or "en"
            lines: list[str] = []
            for a in notice.actions:
                win = event_window(a)
                if win and (clashes := find_clashes(slots, *win)):
                    lines.append(clash_text(a.label, win[0], win[1], clashes, lang))
            for row in notice.timetable:
                if not row.on_date:
                    continue
                for sl in slots:
                    if sl.weekday == row.on_date.weekday() and subject_matches(sl.subject, row.subject):
                        lines.append(changed_class_text(sl, row.on_date, row.start, row.room, row.subject or sl.subject))
            if not lines:
                continue
            key = f"clash:{notice.id}:{st.id}"
            if key in claimed:
                continue
            d = Delivery(dedupe_key=key, notice_id=notice.id, student_id=st.id, kind="clash", status=DeliveryStatus.queued)
            s.add(d)
            await s.commit()  # claimed before sending, so a retried job never alerts twice
            text = " ".join(lines)
            res = await notify_student(s, st, kind="clash" if any(l.startswith(("Clash", "सावध", "ध्यान")) for l in lines) else "update", title="Affects your timetable", text=text, speak=text, lang=lang, url=link)
            d.status = DeliveryStatus.sent if (res["whatsapp"] or res["push"]) else DeliveryStatus.failed
            audit(s, "clash.alert", notice=notice, student_id=st.id, items=len(lines), whatsapp=res["whatsapp"], push=res["push"])
            await s.commit()


async def route_link_for(s: AsyncSession, college_id: str | None, venue: str | None) -> str | None:
    if not college_id or not venue:
        return None
    routes = (await s.scalars(select(Route).where(Route.college_id == college_id, Route.status == "published"))).all()
    for r in routes:
        if place_matches(venue, r.to_place):
            return f"{get_settings().frontend_base_url.rstrip('/')}/r/{r.public_id}"
    return None


async def send_exam_eve(s: AsyncSession, r: Reminder) -> None:
    cfg = get_settings()
    action = await s.get(NoticeAction, r.action_id)
    notice = await load_notice(s, r.notice_id)
    student = await s.get(Student, r.student_id)
    if not action or not notice or not student or notice.status != NoticeStatus.published or student.opted_out_at or not student.reminders_enabled:
        r.status = "cancelled"
        await s.commit()
        return
    lang = student.preferred_lang or "en"
    checklist = [x.strip() for x in cfg.exam_eve_checklist.split(",") if x.strip()]
    link = await route_link_for(s, notice.college_id, action.venue)
    text = exam_eve_text(action.label, action.due_date, action.due_time, action.venue, checklist, link, lang)
    res = await notify_student(s, student, kind="exam", title="Exam tomorrow", text=text, speak=re.sub(r"https?://\S+", "", text), lang=lang, url=link or notice_link(notice))
    r.status = "sent" if (res["whatsapp"] or res["push"]) else "failed"
    audit(s, "exam_eve.sent", notice=notice, student_id=student.id, whatsapp=res["whatsapp"], push=res["push"])
    await s.commit()


async def send_personal_reminder(s: AsyncSession, r: Reminder) -> None:
    pd = await s.get(PersonalDeadline, r.personal_id)
    student = await s.get(Student, r.student_id)
    if not pd or not student or pd.status != "confirmed" or student.opted_out_at or not student.reminders_enabled:
        r.status = "cancelled"
        await s.commit()
        return
    days = (pd.due_date - today_local()).days
    lead = "Today" if days <= 0 else "Tomorrow" if days == 1 else f"In {days} days"
    when = speak_date(pd.due_date) + (f" at {speak_time(pd.due_time)}" if pd.due_time else "")
    text = f"Your reminder. {lead}: {pd.label}. Due {when}."
    res = await notify_student(s, student, kind="personal", title="Your reminder", text=text, speak=text, lang=student.preferred_lang)
    r.status = "sent" if (res["whatsapp"] or res["push"]) else "failed"
    await s.commit()


# ---------------------------------------------------------------------------------------
# 6. Dead jobs: tell humans clearly
# ---------------------------------------------------------------------------------------


FRIENDLY = {
    "unsupported_file": "This file type is not supported.",
    "too_many_pages": "The PDF has too many pages.",
}


async def on_job_dead(job: Job, error: Exception) -> None:
    code = getattr(error, "code", "failed")
    msg = str(error)
    async with sessionmaker()() as s:
        if job.type in ("process_notice", "publish_notice") and job.notice_id:
            notice = await s.get(Notice, job.notice_id)
            if notice:
                notice.status = NoticeStatus.failed
                notice.stage = None
                notice.error_code = code
                notice.error_message = FRIENDLY.get(code, msg)[:1000]
                audit(s, "notice.failed", notice=notice, step=job.type, error=msg[:500])
                await s.commit()
                emit_notice(notice)
        elif job.type == "deliver":
            d = await s.get(Delivery, job.payload.get("delivery_id"))
            if d:
                d.status = DeliveryStatus.failed
                d.error = msg[:1000]
                await s.commit()
        elif job.type == "send_reminder":
            r = await s.get(Reminder, job.payload.get("reminder_id"))
            if r:
                r.status = "failed"
                await s.commit()
        elif job.type == "process_snap":
            from ..models import Snap

            sn = await s.get(Snap, job.payload.get("snap_id"))
            if sn:
                sn.status, sn.error = "failed", msg[:500]
                await s.commit()
        elif job.type == "check_forward_web":
            fc = await s.get(ForwardCheck, job.payload.get("check_id"))
            if fc:
                fc.result = "error"
                await s.commit()
                bus.publish("check.done", {"id": fc.id, "result": "error"}, college_id=fc.college_id)


# Import for side effect: registers the inbound bot and snap handlers.
from . import bot, snapjob  # noqa: F401

_ = (College, StaffUser, PipelineError)  # re-exported names used by bot/routers
