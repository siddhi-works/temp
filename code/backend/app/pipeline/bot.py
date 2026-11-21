"""The WhatsApp bot: what happens when a student messages us.

Commands work in English, Marathi and Hindi. Anything that is not a command is
treated as a question and answered only from published notices.
"""

from __future__ import annotations

import logging
import re
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import sessionmaker, utcnow
from ..errors import AppError, PermanentError
from ..models import (
    College,
    Delivery,
    DeliveryStatus,
    ForwardCheck,
    InboundMessage,
    Job,
    Notice,
    NoticeAction,
    NoticeSource,
    NoticeStatus,
    Student,
    Verification,
)
from ..providers import whatsapp
from ..providers.base import ProviderError
from ..providers.llm import gemini_answer
from ..providers.media import prepare_document, sniff_mime
from ..providers.ocr import run_ocr
from ..providers.stt import transcribe
from ..providers.tts import synthesize
from ..security import decrypt_phone, sign_student_token
from ..storage import get_storage, sha256_bytes
from . import campus
from .dates import speak_date
from .jobs import enqueue
from .notify import notice_link, t
from .personal import resolve_phrase

log = logging.getLogger("suchana.bot")

CMD = {
    "help": {"help", "hi", "hello", "menu", "मदत", "मदद", "नमस्कार", "नमस्ते"},
    "deadlines": {"deadlines", "deadline", "dates", "due", "मुदत", "तारखा", "तारीख", "तारीखें"},
    "inbox": {"inbox", "link", "notices", "सूचना", "सूचनाएं"},
    "stop": {"stop", "unsubscribe", "बंद", "थांबा", "रोको"},
    "start": {"start", "subscribe", "सुरू", "शुरू"},
    "now": {"now", "right now", "आता", "अभी", "अब"},
    "catch": {"catch", "remember", "note", "लक्षात ठेवा", "याद रखो", "नोंद"},
    "yes": {"yes", "y", "ok", "okay", "yeah", "हो", "होय", "हां", "हाँ", "ठीक"},
    "no": {"no", "n", "cancel", "नाही", "नहीं", "नको", "रद्द"},
    "route": {"route", "routes", "map", "मार्ग", "रास्ता", "नकाशा"},
    "timetable": {"timetable", "time table", "वेळापत्रक", "समय सारणी"},
}
BATCH_RE = re.compile(r"^(?:batch|बॅच|बैच)\s+(.{1,30})$", re.IGNORECASE)
BOARD_WORDS = ("board", "snap", "फळा", "फलक", "बोर्ड", "ब्लैकबोर्ड", "lecture", "notes")
LANG_WORDS = {"mr": {"marathi", "मराठी", "lang mr"}, "hi": {"hindi", "हिंदी", "lang hi"}, "en": {"english", "इंग्रजी", "lang en"}}
JOIN_RE = re.compile(r"^(?:join|जॉईन|जॉइन)\s+([a-z0-9\-]{2,16})$", re.IGNORECASE)


def classify(text: str) -> tuple[str, str | None]:
    clean = re.sub(r"\s+", " ", text.strip().lower())
    m = JOIN_RE.match(clean)
    if m:
        return "join", m.group(1).upper()
    m = BATCH_RE.match(clean)
    if m:
        return "batch", m.group(1).strip().upper()
    for lang, words in LANG_WORDS.items():
        if clean in words:
            return "lang", lang
    for cmd, words in CMD.items():
        if clean in words:
            return cmd, None
    return "question", None


async def reply(phone: str, text: str) -> None:
    await whatsapp.send_text(phone, text)


async def upcoming_actions(s: AsyncSession, student: Student, days: int = 45) -> list[tuple[NoticeAction, Notice]]:
    if not student.college_id:
        return []
    from .steps import today_local

    today = today_local()
    rows = (
        await s.execute(
            select(NoticeAction, Notice)
            .join(Notice, Notice.id == NoticeAction.notice_id)
            .where(
                Notice.college_id == student.college_id,
                Notice.status == NoticeStatus.published,
                NoticeAction.due_date.is_not(None),
                NoticeAction.due_date >= today,
                NoticeAction.due_date <= today + timedelta(days=days),
            )
            .order_by(NoticeAction.due_date)
            .limit(10)
        )
    ).all()
    tags = set(student.audience_tags or [])
    return [(a, n) for a, n in rows if not n.audience_tags or not tags or tags & set(n.audience_tags)]


def deadlines_text(items: list[tuple[NoticeAction, Notice]]) -> str:
    if not items:
        return "No upcoming deadlines in your college's published notices."
    lines = ["Upcoming:"]
    for i, (a, n) in enumerate(items, 1):
        check = " Please confirm with the office." if a.verification == Verification.needs_check else ""
        lines.append(f"{i}. {speak_date(a.due_date)}: {a.label} ({n.title}).{check}")
    return "\n".join(lines)


_ANSWERS: dict[tuple, str] = {}


async def answer_question(s: AsyncSession, student: Student, question: str) -> str:
    mem = await campus.answer_from_memory(s, student, question)
    if mem:
        return mem["text"] + "\n\n(From your saved board photos.)"
    items = await upcoming_actions(s, student, days=120)
    notices = (
        await s.scalars(
            select(Notice)
            .where(Notice.college_id == student.college_id, Notice.status == NoticeStatus.published)
            .order_by(Notice.published_at.desc())
            .limit(6)
        )
    ).all() if student.college_id else []
    if not notices:
        return "I could not find any published notices for your college yet. Send JOIN followed by your college code to connect."
    # The same question about the same notices is answered once; a new or changed notice changes the key.
    cache_key = (student.college_id, student.preferred_lang, re.sub(r"\W+", " ", question.lower()).strip(), tuple(n.id for n in notices), tuple(n.version for n in notices))
    if cache_key in _ANSWERS:
        return _ANSWERS[cache_key]
    context_lines = []
    for n in notices:
        context_lines.append(f"[{n.title}] issued by {n.issuer or 'college'}; summary: {(n.summaries or {}).get('en', '')}")
    for a, n in items:
        context_lines.append(f"- {a.label}: {a.due_date.isoformat()}{' ' + a.due_time if a.due_time else ''} ({n.title}; {'verified' if a.verification != 'needs_check' else 'unconfirmed'})")
    try:
        answer = await gemini_answer(question, "\n".join(context_lines), student.preferred_lang)
        if len(_ANSWERS) > 300:
            _ANSWERS.clear()
        _ANSWERS[cache_key] = answer
        return answer
    except ProviderError as exc:
        log.warning("Q&A unavailable, falling back to deadline list: %s", exc)
        return "I can't answer questions right now. " + deadlines_text(items[:5])


async def resend_deferred(s: AsyncSession, student: Student) -> int:
    """The 24h window just opened: send anything we had to hold back."""
    held = (await s.scalars(select(Delivery).where(Delivery.student_id == student.id, Delivery.status == DeliveryStatus.deferred_window))).all()
    for d in held:
        d.status = DeliveryStatus.queued
        await enqueue(s, "deliver", {"delivery_id": d.id}, dedupe_key=f"deliver:{d.id}:retry:{int(utcnow().timestamp())}", notice_id=d.notice_id)
    return len(held)


async def forward_count_today(s: AsyncSession, student: Student) -> int:
    since = utcnow() - timedelta(hours=24)
    return int(
        await s.scalar(select(func.count(ForwardCheck.id)).where(ForwardCheck.student_id == student.id, ForwardCheck.created_at >= since))
        or 0
    )


from .steps import handler


@handler("handle_inbound")
async def handle_inbound(job: Job) -> None:
    get_settings()
    async with sessionmaker()() as s:
        msg = await s.get(InboundMessage, job.payload["inbound_id"])
        if msg is None or msg.status == "done":
            return
        student = await s.get(Student, msg.student_id) if msg.student_id else None
        if student is None:
            raise PermanentError("inbound message has no student")
        phone = decrypt_phone(student.phone_enc)
        student.last_inbound_at = msg.received_at
        resent = await resend_deferred(s, student)
        await s.commit()
        await whatsapp.mark_read(job.payload.get("wa_message_id", ""))
        lang = student.preferred_lang or "en"

        # If a reply fails, the job retries the whole message. Every branch is safe to
        # repeat: join/lang/stop are idempotent and OCR results are cached.
        if msg.msg_type == "text" and msg.text:
            await handle_text(s, student, phone, msg.text, lang, resent)
        elif msg.msg_type == "image" and msg.text and any(w in msg.text.lower() for w in BOARD_WORDS):
            await handle_snap(s, student, phone, msg)
        elif msg.msg_type in ("image", "document"):
            await handle_forward(s, student, phone, msg)
        elif msg.msg_type == "audio":
            try:
                data, mime = await whatsapp.download_media(msg.media_id)
                question = await transcribe(data, mime)
            except ProviderError:
                await reply(phone, "Sorry, I could not understand the voice message. Please try again or type your question.")
            else:
                if student.catch_until and student.catch_until > utcnow():
                    pd, readback = await campus.create_catch(s, student, question, "voice")
                    await reply(phone, f'I heard: "{question}"\n\n{readback}')
                    msg.status = "done"
                    await s.commit()
                    return
                answer = await answer_question(s, student, question)
                await reply(phone, f'You asked: "{question}"\n\n{answer}')
                try:
                    audio = await synthesize(answer, lang)
                    from ..models import Rendition
                    from .steps import send_voice

                    await send_voice(phone, Rendition(lang=lang, text=answer, audio_key=audio.audio_key, voice_note_key=audio.voice_note_key))
                except ProviderError as exc:
                    log.info("spoken answer skipped: %s", exc)
        else:
            await reply(phone, t("menu", lang))
        msg.status = "done"
        await s.commit()


async def handle_text(s: AsyncSession, student: Student, phone: str, text: str, lang: str, resent: int) -> None:
    kind, arg = classify(text)
    if kind == "join":
        college = await s.scalar(select(College).where(College.code == arg))
        if not college:
            await reply(phone, f"No college found with code {arg}. Please check the code with your college office.")
            return
        student.college_id = college.id
        student.consent_at = student.consent_at or utcnow()
        student.opted_out_at = None
        await s.commit()
        await reply(
            phone,
            f"You are connected to {college.name}. You will get its official notices here as voice notes and accessible pages, "
            "with reminders before deadlines. We store your phone number (encrypted) and your language choice only. "
            "Notices are read by AI services (Sarvam, Google Gemini) and checked by college staff before sending.\n\n" + t("menu", lang),
        )
    elif kind == "lang":
        student.preferred_lang = arg
        await s.commit()
        await reply(phone, {"mr": "भाषा मराठी केली.", "hi": "भाषा हिंदी कर दी गई.", "en": "Language set to English."}[arg] + "\n" + t("menu", arg))
    elif kind == "stop":
        student.opted_out_at = utcnow()
        await s.commit()
        await reply(phone, "You will not get notices or reminders any more. Send START to turn them back on.")
    elif kind == "start":
        student.opted_out_at = None
        await s.commit()
        await reply(phone, "Notices and reminders are on again.")
    elif kind == "deadlines":
        await reply(phone, deadlines_text(await upcoming_actions(s, student)))
    elif kind == "inbox":
        base = get_settings().frontend_base_url.rstrip("/")
        await reply(phone, f"Your notices: {base}/me?t={sign_student_token(student.id)}")
    elif kind == "now":
        await reply(phone, (await campus.now_summary(s, student))["text"])
    elif kind == "timetable":
        await handle_batch(s, student, phone, None)
    elif kind == "batch":
        await handle_batch(s, student, phone, arg)
    elif kind == "catch":
        student.catch_until = utcnow() + timedelta(minutes=5)
        await s.commit()
        await reply(phone, "Ready. Send a voice note or type what the teacher said, for example: assignment due next Friday.")
    elif kind in ("yes", "no"):
        await handle_yes_no(s, student, phone, kind)
    elif kind == "route":
        base = get_settings().frontend_base_url.rstrip("/")
        await reply(phone, f"Campus walkthroughs for your college: {base}/routes?c={student.college_id}" if student.college_id else "Send JOIN followed by your college code first.")
    elif kind == "help":
        if not student.college_id:
            await reply(phone, "Welcome to Saathi Campus. Send JOIN followed by your college code (for example JOIN FERG) to get your college's notices.")
        else:
            await reply(phone, ("Sent the notices you missed above.\n\n" if resent else "") + t("menu", lang))
    else:
        armed = student.catch_until and student.catch_until > utcnow()
        pending = await campus.latest_pending(s, student.id)
        if armed:
            pd, readback = await campus.create_catch(s, student, text, "text")
            await reply(phone, readback)
        elif pending is not None and (pending.needs_date or (len(text) < 60 and resolve_phrase(text, campus.local_now().date()).due)):
            await reply(phone, await campus.fix_date(s, pending, text) + " Reply YES to save or NO to cancel.")
        else:
            await reply(phone, await answer_question(s, student, text))


async def handle_yes_no(s: AsyncSession, student: Student, phone: str, kind: str) -> None:
    pending = await campus.latest_pending(s, student.id)
    if pending is None:
        await reply(phone, "Nothing is waiting for a yes or no. Send CATCH to save a deadline, or HELP.")
        return
    if kind == "no":
        pending.status = "cancelled"
        await s.commit()
        await reply(phone, "Cancelled. Send CATCH to try again.")
        return
    try:
        made = await campus.confirm_catch(s, student, pending)
    except AppError as exc:
        await reply(phone, str(exc))
        return
    await reply(phone, campus.confirmation_text(pending, len(made)))


async def handle_batch(s: AsyncSession, student: Student, phone: str, batch: str | None) -> None:
    try:
        n, _ = await campus.import_timetable(s, student, batch)
    except AppError as exc:
        await reply(phone, str(exc) + (" Reply BATCH followed by your batch name." if exc.code == "batch_required" else ""))
        return
    await reply(phone, f"Your timetable is saved: {n} classes. Send NOW any time to hear what is on. I will warn you only when a new notice clashes with your classes.")


async def handle_snap(s: AsyncSession, student: Student, phone: str, msg: InboundMessage) -> None:
    try:
        data, _mime = await whatsapp.download_media(msg.media_id)
    except ProviderError as exc:
        if exc.retryable:
            raise
        await reply(phone, "I could not open that photo. Please send it again.")
        return
    if sniff_mime(data) not in ("image/jpeg", "image/png", "image/webp"):
        await reply(phone, "Please send the board as a photo.")
        return
    sn = await campus.create_snap(s, student, data)
    await reply(phone, f"Saved{' under ' + sn.subject if sn.subject else ''}. I am reading it now. Later you can ask me about it by voice or text.")


async def handle_forward(s: AsyncSession, student: Student, phone: str, msg: InboundMessage) -> None:
    s_ = get_settings()
    if await forward_count_today(s, student) >= s_.student_daily_forward_limit:
        await reply(phone, "You have checked many notices today. Please try again tomorrow.")
        return
    try:
        data, _mime = await whatsapp.download_media(msg.media_id)
    except ProviderError as exc:
        if exc.retryable:
            raise
        await reply(phone, "I could not open that file. Please send the photo or PDF again.")
        return
    await reply(phone, "Got it. Reading this notice now, it takes about half a minute.")
    if sniff_mime(data) is None:
        await reply(phone, "I can read PDFs and photos only. Please send the notice as a photo or PDF.")
        return
    try:
        doc = prepare_document(data, max_pages=s_.max_pdf_pages, max_side=s_.max_image_side_px)
    except PermanentError as exc:
        await reply(phone, str(exc))
        return
    try:
        ocr = await run_ocr(doc, "mr")
    except ProviderError:
        await reply(phone, "Sorry, I could not read this notice right now. I'll keep trying and message you.")
        raise

    from .steps import match_text, send_voice

    result, score, matched, latest = await match_text(s, ocr.text, student.college_id)
    s.add(
        ForwardCheck(
            channel="whatsapp",
            student_id=student.id,
            college_id=student.college_id,
            source_sha256=sha256_bytes(doc.data),
            result=result,
            similarity=score,
            matched_notice_id=matched.id if matched else None,
            latest_notice_id=latest.id if latest else None,
        )
    )
    await s.commit()

    lang = student.preferred_lang or "en"
    token = sign_student_token(student.id)
    if result in ("official", "superseded") and latest is not None:
        from .steps import load_notice

        latest_full = await load_notice(s, latest.id)
        if result == "official":
            head = f"This is an official, current notice: {latest_full.title}."
        else:
            head = (
                f"Careful: this is an OLD version. It was replaced"
                f"{' on ' + speak_date(latest.published_at.date()) if latest.published_at else ''}.\n"
            )
            if latest_full.changes_from_previous:
                head += "What changed:\n" + "\n".join(f"- {c}" for c in latest_full.changes_from_previous[:5])
        rendition = next((r for r in latest_full.renditions if r.lang == lang and r.audio_key), None) or next(
            (r for r in latest_full.renditions if r.audio_key), None
        )
        try:
            await send_voice(phone, rendition)
        except ProviderError as exc:
            log.warning("voice for forward check failed: %s", exc)
        await reply(phone, f"{head}\n\nLatest version: {notice_link(latest_full, token)}")
        return

    # Not an official notice we know: read it for them (unverified) and queue it for a volunteer.
    key = await get_storage().put(f"forwards/{sha256_bytes(doc.data)}{doc.ext}", doc.data)
    n = Notice(
        college_id=student.college_id,
        source=NoticeSource.forward,
        status=NoticeStatus.queued,
        source_file_key=key,
        source_mime=doc.mime,
        source_sha256=sha256_bytes(doc.data),
        page_count=doc.pages,
        ocr_text=ocr.text,
        ocr_engine=ocr.engine,
        forwarded_by_student_id=student.id,
    )
    s.add(n)
    await s.flush()
    await enqueue(s, "process_notice", {"notice_id": n.id}, dedupe_key=f"process:{n.id}", notice_id=n.id)
    await s.commit()
