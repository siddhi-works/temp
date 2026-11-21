"""Student app API. Students sign in with the signed link the bot sends (?t=...); no passwords."""

from __future__ import annotations

import asyncio
import base64
import re
from datetime import date

from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session
from ..errors import AppError, NotFound, PermanentError
from ..models import PersonalDeadline, PushSubscription, Snap, Student
from ..pipeline import campus
from ..pipeline.buzz import BUZZ_TYPES, catalog, valid_pattern
from ..pipeline.dates import find_dates, first_title_line, rule_based_actions, speak_date, verify_action
from ..pipeline.profile import parse_profile, rule_based_personal, verify_personal
from ..pipeline.steps import today_local
from ..providers.base import ProviderError
from ..providers.llm import gemini_extract
from ..providers.media import prepare_document, sniff_audio, sniff_mime
from ..providers.ocr import pdf_text_layer, run_ocr
from ..providers.stt import transcribe
from ..providers.tts import speak_uncached
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


FALLBACK_INTRO = {
    "en": "I could not summarise this notice, so here is the start of its text.",
    "hi": "मैं इस सूचना का सार नहीं बना सका, इसलिए इसका शुरुआती पाठ पढ़ रहा हूँ।",
    "mr": "मला या सूचनेचा सारांश करता आला नाही, म्हणून तिचा सुरुवातीचा मजकूर वाचत आहे.",
}
PARTIAL = {
    "en": "This file has {total} pages. I read only the first {n}.",
    "hi": "इस फ़ाइल में {total} पन्ने हैं। मैंने सिर्फ़ पहले {n} पढ़े हैं।",
    "mr": "या फाईलमध्ये {total} पाने आहेत. मी फक्त पहिली {n} वाचली आहेत.",
}


PERSONAL_HEAD = {"en": "For you:", "hi": "आपके लिए:", "mr": "तुमच्यासाठी:"}
PERSONAL_NONE = {
    "en": "I could not find anything in this notice that is specifically for you.",
    "hi": "मुझे इस सूचना में आपके लिए खास कुछ नहीं मिला।",
    "mr": "मला या सूचनेत तुमच्यासाठी खास काही सापडले नाही.",
}
PERSONAL_CHECK = {
    "en": "Please double-check this on the notice itself.",
    "hi": "कृपया सूचना में खुद भी जाँच लें।",
    "mr": "कृपया सूचनेत स्वतः तपासून घ्या.",
}
PERSONAL_RULE = {
    "en": "I found a line with your details:",
    "hi": "मुझे आपके विवरण वाली यह पंक्ति मिली:",
    "mr": "मला तुमचे तपशील असलेली ही ओळ सापडली:",
}


_WEEKDAYS = {
    "hi": ["सोमवार", "मंगलवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
    "mr": ["सोमवार", "मंगळवार", "बुधवार", "गुरुवार", "शुक्रवार", "शनिवार", "रविवार"],
}
_MONTHS = {
    "hi": ["जनवरी", "फरवरी", "मार्च", "अप्रैल", "मई", "जून", "जुलाई", "अगस्त", "सितंबर", "अक्टूबर", "नवंबर", "दिसंबर"],
    "mr": ["जानेवारी", "फेब्रुवारी", "मार्च", "एप्रिल", "मे", "जून", "जुलै", "ऑगस्ट", "सप्टेंबर", "ऑक्टोबर", "नोव्हेंबर", "डिसेंबर"],
}


def speak_date_lang(d: date, lang: str) -> str:
    """'Monday, 30 November 2026' in the student's language (Marathi with Devanagari digits)."""
    if lang not in _MONTHS:
        return speak_date(d)
    day, year = str(d.day), str(d.year)
    if lang == "mr":
        digits = str.maketrans("0123456789", "०१२३४५६७८९")
        day, year = day.translate(digits), year.translate(digits)
    return f"{_WEEKDAYS[lang][d.weekday()]}, {day} {_MONTHS[lang][d.month - 1]} {year}"


def _plain_text(text: str) -> str:
    """Scanned tables arrive as HTML. Turn cells into comma-separated speech and drop markup, so nothing reads '<td>' aloud."""
    t = re.sub(r"</t[dh]\s*>", ", ", text, flags=re.I)
    t = re.sub(r"</tr\s*>|<br\s*/?>", ". ", t, flags=re.I)
    t = re.sub(r"<[^>]*>?", " ", t)  # tags, including ones whose closing '>' was lost
    t = re.sub(r"[#*|_`~]+", " ", t)
    t = re.sub(r"(,\s*){2,}", ", ", t)
    t = re.sub(r"(\.\s*){2,}", ". ", t)
    return re.sub(r"\s+", " ", t).strip()


def _has_text_layer(reader) -> bool:
    """Sample first, middle and last page: a PDF exported from Word or PowerPoint has real text on every page, a scan has none."""
    n = len(reader.pages)
    try:
        for i in sorted({0, n // 2, n - 1}):
            t = reader.pages[i].extract_text() or ""
            if len(t.strip()) < 150 or "(cid:" in t:
                return False
    except Exception:  # noqa: BLE001
        return False
    return True


def _first_pages(data: bytes, cfg, force_scan: bool = False) -> tuple[bytes, tuple[int, int] | None, int]:
    """Smarter page limit. Scanned pages are paid OCR, so they are capped (MAX_PDF_PAGES). PDFs that already contain
    text are read for free, so they may be longer (MAX_TEXT_PDF_PAGES). Returns (pdf, (pages read, pages in file) if cut, limit)."""
    import io

    from pypdf import PdfReader, PdfWriter

    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise AppError("This PDF is password protected, so I cannot read it. Please send an unlocked copy.", code="pdf_locked", status_code=415)
        total = len(reader.pages)
        limit = cfg.max_pdf_pages
        if not force_scan and total > limit and _has_text_layer(reader):
            limit = cfg.max_text_pdf_pages
        if total <= limit:
            return data, None, limit
        writer = PdfWriter()
        for page in reader.pages[:limit]:
            writer.add_page(page)
        out = io.BytesIO()
        writer.write(out)
        return out.getvalue(), (limit, total), limit
    except AppError:
        raise
    except Exception:  # noqa: BLE001  (damaged: let prepare_document report it)
        return data, None, cfg.max_pdf_pages


def _prepare(raw: bytes, cfg):
    """Returns (document, truncation info, free text layer or None). Cost and time stay bounded for any upload."""
    data, truncated, limit = raw, None, cfg.max_pdf_pages
    if sniff_mime(raw) == "application/pdf":
        data, truncated, limit = _first_pages(raw, cfg)
    doc = prepare_document(data, max_pages=limit, max_side=cfg.max_image_side_px)
    layer = None
    if limit > cfg.max_pdf_pages:
        layer = pdf_text_layer(doc)
        if layer is None:  # looked like text but is not clean: treat it as a scan so cost stays capped
            data, truncated, limit = _first_pages(raw, cfg, force_scan=True)
            doc = prepare_document(data, max_pages=limit, max_side=cfg.max_image_side_px)
    return doc, truncated, layer


_READ_SLOTS = asyncio.Semaphore(2)  # the free host has little memory: at most two notices are processed at once, others wait their turn


@router.post("/read")
async def read_notice(
    request: Request,
    file: UploadFile = File(...),
    lang: str = Form("en"),
    profile: str | None = Form(None),
    student: Student = Depends(current_student),
):
    """Read any notice (photo or PDF) aloud. Nothing is published, shared or stored; dates are checked against the scan.
    `profile` (optional JSON, kept only on the student's phone) lets the answer say what applies to that student."""
    from .public import rate_limit

    rate_limit(request, 6, key=f"read:{student.id}")  # per student, not per address
    cfg = get_settings()
    lang = lang if lang in CAUTION else "en"
    limit = cfg.max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(f"That file is larger than {cfg.max_upload_mb} MB. Please send a smaller one.", code="file_too_large", status_code=413)
    if len(data) < 200:
        raise AppError("That file looks empty. Please choose the notice again.", code="empty_file", status_code=400)
    async with _READ_SLOTS:
        return await _read_notice(data, lang, parse_profile(profile))


async def _read_notice(data: bytes, lang: str, prof=None) -> dict:
    cfg = get_settings()
    try:
        doc, truncated, layer = _prepare(data, cfg)
        ocr = layer or await run_ocr(doc, "mr", cache=False)  # cache=False: the notice text is never saved
    except AppError:
        raise
    except ProviderError as exc:
        raise AppError("I could not read that file just now. Please try again in a minute.", code="read_failed", status_code=502) from exc
    except PermanentError as exc:  # already written for people, e.g. "This file is not a PDF or photo"
        raise AppError(str(exc), code=getattr(exc, "code", "unsupported_file"), status_code=415) from exc
    except Exception as exc:  # noqa: BLE001
        raise AppError("I could not read that file. Please try a clearer photo or PDF.", code="read_failed", status_code=502) from exc

    ref = today_local()
    image = doc.data if doc.mime.startswith("image/") else None
    # Tables and date sheets: let the vision model see the page itself (photos always; short PDFs up to 4 pages).
    has_table = "<table" in ocr.text.lower()
    visual = image if image is not None else (doc.data if doc.mime == "application/pdf" and doc.pages <= 4 and has_table else None)
    if len(ocr.text.strip()) < 10 and image is None:
        raise AppError("I could not find any text in that file. It may be blank or too faint. Please try a clearer scan.", code="no_text", status_code=422)
    ex = None
    try:
        # Hard time limit: the host cuts requests near 100 seconds, so a slow AI answer must fall back to the plain text.
        ex = await asyncio.wait_for(
            gemini_extract(ocr.text, image=visual, image_mime=doc.mime if visual else None, today=ref.isoformat(), profile=prof, lang=lang, force_image=has_table),
            timeout=40,
        )
        title, is_notice, legibility = ex.title, ex.is_notice, ex.legibility
        summary = getattr(ex.summaries, lang) or ex.summaries.en
        raw_actions = [a.model_dump() for a in ex.actions]
    except Exception as exc:  # noqa: BLE001  (Gemini busy, or its answer was cut off on a long document)
        if not ocr.text.strip():
            raise AppError("I could not read any text in that file. Please try a clearer photo or PDF.", code="read_failed", status_code=502) from exc
        # Fallback: the scan was read, so speak the start of the text itself rather than failing.
        title, is_notice, legibility = first_title_line(ocr.text), True, "partial"
        summary = FALLBACK_INTRO[lang] + " " + _plain_text(ocr.text)[:600]
        raw_actions = rule_based_actions(ocr.text, ref)

    actions, needs_check = [], False
    for a in raw_actions:
        v = verify_action(due_date=a.get("due_date"), due_time=a.get("due_time"), amount=a.get("amount"), source_quote=a.get("source_quote"), ocr_text=ocr.text, ref=ref)
        needs_check = needs_check or v.status != "verified"
        actions.append(
            {
                "kind": a.get("kind"),
                "label": a.get("label"),
                "due_date": a.get("due_date"),
                "date_spoken": speak_date_lang(date.fromisoformat(a["due_date"]), lang) if a.get("due_date") and v.status == "verified" else None,
                "due_time": a.get("due_time"),
                "venue": a.get("venue"),
                "status": v.status,
                "reasons": v.reasons,
            }
        )

    # The spoken summary is AI text too: any date in it must exist in the scan, and any weekday must match its date.
    scan_dates = {f.value for f in find_dates(ocr.text, ref)} | {f.alt_value for f in find_dates(ocr.text, ref) if f.alt_value}
    for f in find_dates(summary, ref):
        if f.value not in scan_dates or (f.weekday_written is not None and f.weekday_written != f.value.weekday()):
            needs_check = True

    # Personal insight: only claims that are really in the scan AND really about this student are spoken.
    personal, personal_spoken = None, ""
    if prof is not None and is_notice:
        ai = getattr(ex, "personal", None)
        matches = verify_personal(ai.matches, ocr.text, prof) if ai is not None else []
        by_rule = False
        if not matches:
            matches, by_rule = rule_based_personal(ocr.text, prof), True
        if not matches:
            personal_spoken = PERSONAL_NONE[lang]
        elif by_rule:
            personal_spoken = f"{PERSONAL_RULE[lang]} " + " ".join(m.quote[:220] + "." for m in matches[:2]) + f" {PERSONAL_CHECK[lang]}"
        else:
            personal_spoken = f"{PERSONAL_HEAD[lang]} " + " ".join(f"{m.what}: {m.detail}." for m in matches[:3]) + f" {PERSONAL_CHECK[lang]}"
        personal = {
            "found": bool(matches),
            "matches": [{"what": m.what, "detail": m.detail, "quote": m.quote, "basis": m.basis} for m in matches],
        }

    if not is_notice:
        spoken = NOT_NOTICE[lang]
    else:
        spoken = f"{personal_spoken} {summary}".strip()
        if needs_check or legibility != "good":
            spoken = f"{spoken} {CAUTION[lang]}"
    if truncated:
        spoken = f"{spoken} {PARTIAL[lang].format(n=truncated[0], total=truncated[1])}"
    # Spoken here, returned inside the answer and never stored: personal details must not end up in a cache or a file.
    mp3 = await speak_uncached(spoken, lang)
    return {
        "title": title,
        "is_notice": is_notice,
        "lang": lang,
        "spoken_text": spoken,
        "legibility": legibility,
        "actions": actions,
        "needs_check": needs_check,
        "personal": personal,
        "audio_b64": base64.b64encode(mp3).decode() if mp3 else None,
        "audio_mime": "audio/mpeg",
        "audio_url": None,
        "pages_read": truncated[0] if truncated else getattr(ocr, "pages", None),
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

