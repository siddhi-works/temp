"""Student app API. Students sign in with the signed link the bot sends (?t=...); no passwords."""

from __future__ import annotations

import base64
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..auth_deps import student_token
from ..config import get_settings
from ..db import get_session
from ..errors import AppError, NotFound
from ..models import PersonalDeadline, PushSubscription, Snap, Student
from ..pipeline import campus, linking, reader
from ..pipeline.buzz import BUZZ_TYPES, catalog, valid_pattern
from ..pipeline.dates import speak_date
from ..pipeline.profile import parse_profile
from ..providers.base import ProviderError
from ..providers.media import sniff_audio, sniff_mime
from ..providers.tts import speak_uncached
from ..providers.stt import transcribe
from ..security import seal, unseal, verify_student_token
from ..storage import get_storage

router = APIRouter(prefix="/api/me", tags=["student"])
public_router = APIRouter(prefix="/api/public", tags=["public"])


async def current_student(t: str = Depends(student_token), s: AsyncSession = Depends(get_session)) -> Student:
    st = await s.get(Student, verify_student_token(t))
    if st is None:
        raise NotFound("Student not found.", code="student_not_found")
    return st


async def read_audio(file: UploadFile, max_mb: int = 5) -> tuple[bytes, str, str]:
    data = await file.read(max_mb * 1024 * 1024 + 1)
    if len(data) > max_mb * 1024 * 1024:
        raise AppError(f"Recording is larger than {max_mb} MB.", code="file_too_large", status_code=413)
    kind = sniff_audio(data)
    if not data or kind is None:
        raise AppError("That does not look like an audio recording.", code="unsupported_audio", status_code=415)
    return data, kind[0], kind[1]


# --------------------------------------------------------------------------- Right Now & timetable


@router.get("/now")
async def now(student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    return await campus.now_summary(s, student)


class SlotIn(BaseModel):
    weekday: int = Field(ge=0, le=6)
    start: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str = Field(pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    subject: str = Field(min_length=1, max_length=200)
    room: str | None = Field(None, max_length=60)


@router.get("/timetable")
async def get_timetable(student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    slots = await campus.slots_for(s, student.id)
    batches: list[str] = []
    if student.college_id:
        notices = await campus.timetable_notices(s, student.college_id)
        batches = campus.timetable_batches(notices[0].timetable) if notices else []
    return {"slots": [sl.__dict__ for sl in sorted(slots, key=lambda x: (x.weekday, x.start))], "available_batches": batches}


@router.put("/timetable")
async def put_timetable(slots: list[SlotIn], student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    for sl in slots:
        if sl.start >= sl.end:
            raise AppError(f"{sl.subject}: the class must end after it starts.", code="bad_slot")
    n = await campus.replace_slots(s, student.id, [sl.model_dump() for sl in slots])
    return {"slots": n}


class ImportIn(BaseModel):
    batch: str | None = None


@router.post("/timetable/import")
async def import_timetable(body: ImportIn, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    n, batches = await campus.import_timetable(s, student, body.batch)
    return {"slots": n, "available_batches": batches}


# --------------------------------------------------------------------------- Verbal Deadline Catcher


def catch_json(pd: PersonalDeadline, readback: str | None = None) -> dict:
    return {
        "id": pd.id,
        "label": pd.label,
        "due_date": pd.due_date.isoformat() if pd.due_date else None,
        "due_date_spoken": speak_date(pd.due_date) if pd.due_date else None,
        "due_time": pd.due_time,
        "status": pd.status,
        "needs_date": pd.needs_date,
        "transcript": pd.transcript,
        "readback": readback,
    }


@router.post("/read")
async def read_notice(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("en"),
    profile: str | None = Form(None),
    student: Student = Depends(current_student),
):
    """Read any notice (photo or PDF) aloud. Nothing is published, shared or stored; dates are checked against the scan.
    `profile` (optional JSON, kept only on the student's phone) lets the answer say what applies to that student.
    The same engine answers on WhatsApp (pipeline/reader.py)."""
    from .public import rate_limit

    rate_limit(request, get_settings().global_reads_per_minute, key="global:read")  # whole-service ceiling
    rate_limit(request, 6, key=f"read:{student.id}")  # per student, not per address
    cfg = get_settings()
    limit = cfg.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(f"That file is larger than {cfg.max_upload_mb} MB. Please send a smaller one.", code="file_too_large", status_code=413)
    if len(data) < 200:
        raise AppError("That file looks empty. Please choose the notice again.", code="empty_file", status_code=400)
    res = await reader.read_notice(data, lang, parse_profile(profile))
    text = res.pop("_ocr_text", "")
    # "Ask your notice" without storing anything: the scan text goes back to the phone encrypted (only this server can open it,
    # and only for an hour). The phone returns it with each question. The server keeps no copy.
    res["ask_token"] = seal(text[:30000]) if res.get("is_notice") and text else None
    return res


@router.post("/ask")
async def ask_notice(
    request: Request,
    ask_token: str = Form(...),
    question: str | None = Form(None),
    audio: UploadFile | None = File(None),
    lang: str = Form("en"),
    student: Student = Depends(current_student),
):
    """Ask a question about the notice just read, by text or voice. Answered from that notice only; every date and number in the answer
    is checked against it. Nothing is stored."""
    from .public import rate_limit

    rate_limit(request, 12, key=f"ask:{student.id}")
    text = unseal(ask_token, 3600)
    if text is None:
        raise AppError("That notice is no longer open. Please read it again to ask about it.", code="ask_expired", status_code=410)
    lang = lang if lang in ("en", "hi", "mr") else "en"
    heard = None
    if audio is not None:
        data, mime, _ = await read_audio(audio)
        try:
            heard = question = await transcribe(data, mime)
        except ProviderError as exc:
            raise AppError("I could not hear that clearly. Please try again, or type it.", code="stt_failed", status_code=502) from exc
    res = await reader.ask_notice(question or "", text, lang)
    mp3 = await speak_uncached(res["answer"], lang)
    return {**res, "question": heard, "lang": lang, "audio_b64": base64.b64encode(mp3).decode() if mp3 else None, "audio_mime": "audio/mpeg"}


# --------------------------------------------------------------------------- Connect WhatsApp


def _whatsapp_available() -> bool:
    cfg = get_settings()
    return bool(cfg.whatsapp_ready and cfg.whatsapp_business_number)


@router.get("/whatsapp")
async def whatsapp_status(student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    """Is this website account connected to WhatsApp, and is a connection waiting to be confirmed?"""
    return {"available": _whatsapp_available(), "open_url": linking.whatsapp_link(), **await linking.link_status(s, student)}


@router.post("/whatsapp/link")
async def whatsapp_link(request: Request, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    """Start connecting: returns the click-to-chat link and its QR code. The code in it is random, single-use and lasts 10 minutes."""
    from .public import rate_limit

    if not _whatsapp_available():
        raise AppError("WhatsApp is not set up on this server yet.", code="whatsapp_unavailable", status_code=503)
    rate_limit(request, 5, key=f"link:{student.id}")
    code, _row = await linking.start_link(s, student)
    url = linking.whatsapp_link(code)
    return {"url": url, "qr_svg": linking.qr_svg(url), "code": code, "expires_in_seconds": int(linking.LINK_TTL.total_seconds())}


class ConfirmIn(BaseModel):
    code: str = Field(min_length=4, max_length=12)


@router.post("/whatsapp/confirm")
async def whatsapp_confirm(request: Request, body: ConfirmIn, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    """Finish connecting: type the 4-digit code the WhatsApp assistant sent back. Five wrong tries cancel it."""
    from .public import rate_limit

    rate_limit(request, 10, key=f"linkc:{student.id}")
    return await linking.confirm_link(s, student, body.code)


@router.delete("/whatsapp")
async def whatsapp_disconnect(student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    """Disconnect WhatsApp at any time. The phone number is forgotten; saved deadlines stay."""
    await linking.disconnect(s, student)
    return {"linked": False}


@router.post("/catch", status_code=201)
async def catch(
    audio: UploadFile | None = File(None),
    text: str | None = Form(None),
    lang: str = Form("en"),
    student: Student = Depends(current_student),
    s: AsyncSession = Depends(get_session),
):
    """One tap: say what the teacher said. Returns a read-back to confirm; nothing is saved until the student confirms."""
    if audio is not None:
        data, mime, _ = await read_audio(audio)
        try:
            transcript = await transcribe(data, mime)
        except ProviderError as exc:
            raise AppError("I could not hear that clearly. Please try again, or type it.", code="stt_failed", status_code=502) from exc
        source = "voice"
    elif text:
        transcript, source = text[:1000], "text"
    else:
        raise AppError("Send a recording or some text.", code="empty_speech")
    pd, readback = await campus.create_catch(s, student, transcript, source, lang if lang in ("en", "hi", "mr") else "en")
    return catch_json(pd, readback)


class CatchConfirm(BaseModel):
    due_date: date | None = None
    due_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    label: str | None = Field(None, max_length=300)
    lang: str = "en"


@router.post("/catch/{catch_id}/confirm")
async def confirm_catch(catch_id: str, body: CatchConfirm | None = None, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    pd = await s.get(PersonalDeadline, catch_id)
    if pd is None or pd.student_id != student.id:
        raise NotFound("That reminder was not found.", code="catch_not_found")
    if body:
        if body.due_date:
            pd.due_date, pd.needs_date = body.due_date, False
        if body.due_time:
            pd.due_time = body.due_time
        if body.label:
            pd.label = body.label
    made = await campus.confirm_catch(s, student, pd)
    lang = body.lang if body and body.lang in ("hi", "mr") else "en"
    return {**catch_json(pd), "reminders": len(made), "message": campus.confirmation_text(pd, len(made), lang)}


@router.post("/catch/{catch_id}/cancel")
async def cancel_catch(catch_id: str, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    pd = await s.get(PersonalDeadline, catch_id)
    if pd is None or pd.student_id != student.id:
        raise NotFound("That reminder was not found.", code="catch_not_found")
    pd.status = "cancelled"
    from ..models import Reminder

    await s.execute(delete(Reminder).where(Reminder.personal_id == pd.id, Reminder.status == "pending"))
    await s.commit()
    return catch_json(pd)


@router.get("/deadlines")
async def my_deadlines(student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    today = campus.local_now().date()
    rows = (
        await s.scalars(
            select(PersonalDeadline)
            .where(PersonalDeadline.student_id == student.id, PersonalDeadline.status == "confirmed", PersonalDeadline.due_date >= today)
            .order_by(PersonalDeadline.due_date)
        )
    ).all()
    return {"deadlines": [catch_json(p) for p in rows]}


# --------------------------------------------------------------------------- Board Snap & Lecture Memory


def snap_json(sn: Snap, full: bool = False) -> dict:
    data = {
        "id": sn.id,
        "subject": sn.subject,
        "room": sn.room,
        "status": sn.status,
        "summary": sn.summary,
        "captured_at": sn.captured_at.isoformat(),
        "error": sn.error,
    }
    if full:
        data["text"] = sn.ocr_text
        data["engine"] = sn.ocr_engine
    return data


@router.post("/snaps", status_code=202)
async def upload_snap(file: UploadFile = File(...), subject: str | None = Form(None), student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError("That photo is too large.", code="file_too_large", status_code=413)
    if sniff_mime(data) not in ("image/jpeg", "image/png", "image/webp"):
        raise AppError("Please send a photo of the board (JPG, PNG or WEBP).", code="unsupported_file", status_code=415)
    sn = await campus.create_snap(s, student, data)
    if subject:
        sn.subject = subject[:200]
        await s.commit()
    return snap_json(sn)


@router.get("/snaps")
async def list_snaps(student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    rows = (await s.scalars(select(Snap).where(Snap.student_id == student.id).order_by(Snap.captured_at.desc()).limit(100))).all()
    return {"snaps": [snap_json(r) for r in rows]}


@router.get("/snaps/{snap_id}")
async def get_snap(snap_id: str, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    sn = await s.get(Snap, snap_id)
    if sn is None or sn.student_id != student.id:
        raise NotFound("Board photo not found.", code="snap_not_found")
    return snap_json(sn, full=True)


@router.delete("/snaps/{snap_id}")
async def delete_snap(snap_id: str, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    sn = await s.get(Snap, snap_id)
    if sn is None or sn.student_id != student.id:
        raise NotFound("Board photo not found.", code="snap_not_found")
    if sn.source_key:
        await get_storage().delete(sn.source_key)
    await s.delete(sn)
    await s.commit()
    return {"ok": True}


@router.get("/memory")
async def memory(q: str = Query(..., min_length=2, max_length=300), student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    ans = await campus.answer_from_memory(s, student, q)
    if ans is None:
        return {"found": False, "text": "I did not find that on any board you saved. Try different words, or photograph the board next time."}
    return {"found": True, **ans}


@router.post("/memory/voice")
async def memory_voice(audio: UploadFile = File(...), student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    data, mime, _ = await read_audio(audio)
    try:
        question = await transcribe(data, mime)
    except ProviderError as exc:
        raise AppError("I could not hear that clearly. Please try again.", code="stt_failed", status_code=502) from exc
    ans = await campus.answer_from_memory(s, student, question)
    if ans is None:
        return {"question": question, "found": False, "text": "I did not find that on any board you saved."}
    return {"question": question, "found": True, **ans}


# --------------------------------------------------------------------------- Buzz Codes & push


@router.get("/buzz-codes")
async def buzz_codes(student: Student = Depends(current_student)):
    return {
        "codes": catalog(student.buzz_prefs),
        "note": "Vibration patterns work on Android phones with Chrome. iPhones do not vibrate for web notifications.",
    }


class BuzzIn(BaseModel):
    pattern: list[int] | None = Field(None, description="milliseconds on/off/on/..., or null to reset")


@router.put("/buzz-codes/{kind}")
async def set_buzz(kind: str, body: BuzzIn, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    if kind not in BUZZ_TYPES:
        raise NotFound("Unknown message type.", code="unknown_buzz_type")
    prefs = dict(student.buzz_prefs or {})
    if body.pattern is None:
        prefs.pop(kind, None)
    elif valid_pattern(body.pattern):
        prefs[kind] = body.pattern
    else:
        raise AppError("A pattern is 1 to 12 numbers, each between 10 and 1000 milliseconds.", code="bad_pattern")
    student.buzz_prefs = prefs or None
    await s.commit()
    return {"codes": catalog(student.buzz_prefs)}


class PushKeys(BaseModel):
    p256dh: str = Field(max_length=200)
    auth: str = Field(max_length=100)


class PushIn(BaseModel):
    endpoint: str = Field(max_length=600, pattern=r"^https://")
    keys: PushKeys


@router.post("/push/subscribe", status_code=201)
async def push_subscribe(body: PushIn, student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    sub = await s.scalar(select(PushSubscription).where(PushSubscription.endpoint == body.endpoint))
    if sub is None:
        sub = PushSubscription(student_id=student.id, endpoint=body.endpoint, p256dh=body.keys.p256dh, auth=body.keys.auth)
        s.add(sub)
    else:
        sub.student_id, sub.p256dh, sub.auth, sub.failures = student.id, body.keys.p256dh, body.keys.auth, 0
    await s.commit()
    return {"ok": True}


@router.delete("/push/subscribe")
async def push_unsubscribe(endpoint: str = Query(...), student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    await s.execute(delete(PushSubscription).where(PushSubscription.endpoint == endpoint, PushSubscription.student_id == student.id))
    await s.commit()
    return {"ok": True}


@router.post("/push/test")
async def push_test(kind: str = "notice", student: Student = Depends(current_student), s: AsyncSession = Depends(get_session)):
    """Lets a student feel each pattern: sends only a push, never WhatsApp."""
    if kind not in BUZZ_TYPES:
        raise NotFound("Unknown message type.", code="unknown_buzz_type")
    from ..pipeline.messaging import push_to_student

    n = await push_to_student(s, student, kind=kind, title=BUZZ_TYPES[kind]["name"], body="This is how this message will feel.")
    return {"sent_to_devices": n}


@public_router.get("/push-key")
async def push_key():
    key = get_settings().vapid_public_key
    if not key:
        raise AppError("Vibration alerts are not set up on this server.", code="push_not_configured", status_code=503)
    return {"public_key": key}

