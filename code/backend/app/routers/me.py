"""Student app API. Students sign in with the signed link the bot sends (?t=...); no passwords."""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..errors import AppError, NotFound
from ..models import PersonalDeadline, PushSubscription, Snap, Student
from ..pipeline import campus
from ..pipeline.buzz import BUZZ_TYPES, catalog, valid_pattern
from ..pipeline.dates import speak_date, verify_action
from ..pipeline.steps import today_local
from ..providers.base import ProviderError
from ..providers.llm import gemini_extract
from ..providers.media import prepare_document, sniff_audio, sniff_mime
from ..providers.ocr import run_ocr
from ..providers.stt import transcribe
from ..providers.tts import synthesize
from ..serializers import media_url
from ..security import verify_student_token
from ..storage import get_storage

router = APIRouter(prefix="/api/me", tags=["student"])
public_router = APIRouter(prefix="/api/public", tags=["public"])


async def current_student(t: str = Query(..., description="signed token from the WhatsApp link"), s: AsyncSession = Depends(get_session)) -> Student:
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


CAUTION = {
    "en": "Some dates could not be checked against the scan. Please confirm them with the college office.",
    "hi": "कुछ तारीखों की स्कैन से पुष्टि नहीं हो सकी। कृपया कॉलेज कार्यालय से पूछ लें।",
    "mr": "काही तारखांची स्कॅनशी खात्री करता आली नाही. कृपया महाविद्यालयाच्या कार्यालयात विचारा.",
}
NOT_NOTICE = {
    "en": "This does not look like a college notice. Please try a clearer photo.",
    "hi": "यह कॉलेज की सूचना जैसी नहीं लगती। कृपया साफ़ फ़ोटो भेजें।",
    "mr": "ही महाविद्यालयाची सूचना वाटत नाही. कृपया स्पष्ट फोटो पाठवा.",
}


@router.post("/read")
async def read_notice(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("en"),
    student: Student = Depends(current_student),
):
    """Read any notice (photo or PDF) aloud. Nothing is published or shared; dates are checked against the scan."""
    from .public import rate_limit

    rate_limit(request, 6)
    cfg = get_settings()
    lang = lang if lang in CAUTION else "en"
    limit = cfg.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError("That file is too large.", code="file_too_large", status_code=413)
    try:
        doc = prepare_document(data, max_pages=cfg.max_pdf_pages, max_side=cfg.max_image_side_px)
        ocr = await run_ocr(doc, "mr")
        image = doc.data if doc.mime.startswith("image/") else None
        ex = await gemini_extract(ocr.text, image=image, image_mime=doc.mime if image else None, today=today_local().isoformat())
    except AppError:
        raise
    except ProviderError as exc:
        raise AppError("I could not read that file just now. Please try again in a minute.", code="read_failed", status_code=502) from exc
    except Exception as exc:  # noqa: BLE001
        if getattr(exc, "code", None) == "unsupported_file":
            raise AppError(str(exc), code="unsupported_file", status_code=415) from exc
        raise AppError("I could not read that file. Please try a clearer photo or PDF.", code="read_failed", status_code=502) from exc

    ref = today_local()
    actions, needs_check = [], False
    for a in ex.actions:
        v = verify_action(due_date=a.due_date, due_time=a.due_time, amount=a.amount, source_quote=a.source_quote, ocr_text=ocr.text, ref=ref)
        needs_check = needs_check or v.status != "verified"
        actions.append(
            {
                "kind": a.kind,
                "label": a.label,
                "due_date": a.due_date,
                "date_spoken": speak_date(date.fromisoformat(a.due_date)) if a.due_date and v.status == "verified" else None,
                "due_time": a.due_time,
                "venue": a.venue,
                "status": v.status,
                "reasons": v.reasons,
            }
        )

    if not ex.is_notice:
        spoken = NOT_NOTICE[lang]
    else:
        spoken = getattr(ex.summaries, lang) or ex.summaries.en
        if needs_check or ex.legibility != "good":
            spoken = f"{spoken} {CAUTION[lang]}"
    audio_url = None
    try:
        tts = await synthesize(spoken, lang)
        audio_url = media_url(tts.audio_key)
    except ProviderError:
        pass  # the text is still returned; the page can read it with the phone's own voice
    return {
        "title": ex.title,
        "is_notice": ex.is_notice,
        "lang": lang,
        "spoken_text": spoken,
        "legibility": ex.legibility,
        "actions": actions,
        "needs_check": needs_check,
        "audio_url": audio_url,
    }


@router.post("/catch", status_code=201)
async def catch(
    audio: UploadFile | None = File(None),
    text: str | None = Form(None),
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
    pd, readback = await campus.create_catch(s, student, transcript, source)
    return catch_json(pd, readback)


class CatchConfirm(BaseModel):
    due_date: date | None = None
    due_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    label: str | None = Field(None, max_length=300)


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
    return {**catch_json(pd), "reminders": len(made), "message": campus.confirmation_text(pd, len(made))}


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

