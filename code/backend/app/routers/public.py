"""Student-facing and public endpoints: accessible notice page, inbox, calendar, forward check, media."""

from __future__ import annotations

import html
import time
from collections import defaultdict, deque
from datetime import timedelta
from pathlib import Path

import markdown as md
from fastapi import APIRouter, Depends, File, Form, Query, Request, UploadFile
from fastapi.responses import FileResponse, HTMLResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import get_session, utcnow
from ..errors import AppError, NotFound, TooManyRequests
from ..models import College, ForwardCheck, Notice, NoticeSource, NoticeStatus, Student
from ..pipeline.dates import speak_date
from ..pipeline.jobs import enqueue
from ..pipeline.steps import latest_in_series, load_notice, today_local
from ..security import verify_student_token
from ..serializers import notice_public_json, notice_summary_json, rendition_json
from ..storage import get_storage, sha256_bytes
from .notices import _read_upload

router = APIRouter(tags=["public"])
env = Environment(loader=FileSystemLoader(Path(__file__).parent.parent / "templates"), autoescape=select_autoescape(["html"]))
LANG_NAMES = {"en": "English", "mr": "मराठी (Marathi)", "hi": "हिन्दी (Hindi)"}
VISIBLE = (NoticeStatus.published, NoticeStatus.superseded)


# --- tiny in-memory rate limiter (per IP) -----------------------------------------------------
_hits: dict[str, deque] = defaultdict(deque)


def rate_limit(request: Request, per_minute: int, key: str | None = None) -> None:
    """Per client address, or per `key` (a student id) so a whole classroom behind one Wi-Fi address is not blocked together."""
    ip = key or request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
    q = _hits[ip]
    now = time.monotonic()
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= per_minute:
        raise TooManyRequests("Too many requests. Please wait a minute and try again.")
    q.append(now)


async def _public_notice(s: AsyncSession, public_id: str) -> Notice:
    n = await s.scalar(select(Notice.id).where(Notice.public_id == public_id))
    notice = await load_notice(s, n) if n else None
    if notice is None or notice.source != NoticeSource.desk or notice.status not in VISIBLE:
        raise NotFound("This notice does not exist or is not published yet.", code="notice_not_found")
    return notice


@router.get("/api/public/notices/{public_id}")
async def public_notice(public_id: str, s: AsyncSession = Depends(get_session)):
    n = await _public_notice(s, public_id)
    data = notice_public_json(n)
    if n.status == NoticeStatus.superseded:
        latest = await latest_in_series(s, n)
        data["latest_public_id"] = latest.public_id if latest.id != n.id else None
    return data


def _render_body(markdown_text: str | None) -> Markup:
    safe = html.escape(markdown_text or "", quote=False)
    return Markup(md.markdown(safe, extensions=["tables", "sane_lists"]))


@router.get("/n/{public_id}", response_class=HTMLResponse)
async def notice_page(public_id: str, s: AsyncSession = Depends(get_session)):
    """Server-rendered accessible page: works on any phone browser with no JavaScript."""
    n = await _public_notice(s, public_id)
    latest = await latest_in_series(s, n) if n.status == NoticeStatus.superseded else None
    lang = (n.extraction or {}).get("language", "en")
    page = env.get_template("notice.html").render(
        n=n,
        latest=latest if latest and latest.id != n.id else None,
        college=n.college.name if n.college else "College",
        approver=(n.approved_by.office or n.approved_by.name) if n.approved_by else None,
        actions=n.actions,
        timetable=n.timetable,
        renditions=[rendition_json(r) for r in n.renditions if r.audio_key],
        speak=speak_date,
        lang_names=LANG_NAMES,
        page_lang="en",
        body_lang=lang if lang in ("en", "mr", "hi") else "mr",
        body_html=_render_body(n.body_markdown),
        engines=", ".join(filter(None, [n.ocr_engine, (n.extraction_engine or "").split(":")[0]])),
        contact=(n.college.disability_cell_contact if n.college and n.college.disability_cell_contact else "the college office"),
    )
    return HTMLResponse(page)


def _ics_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace(";", "\\;").replace(",", "\\,").replace("\n", "\\n")


@router.get("/api/public/notices/{public_id}/calendar.ics")
async def notice_calendar(public_id: str, s: AsyncSession = Depends(get_session)):
    n = await _public_notice(s, public_id)
    stamp = utcnow().strftime("%Y%m%dT%H%M%SZ")
    lines = ["BEGIN:VCALENDAR", "VERSION:2.0", "PRODID:-//Suchana Setu//EN", "CALSCALE:GREGORIAN"]
    for a in n.actions:
        if not a.due_date:
            continue
        lines += ["BEGIN:VEVENT", f"UID:{a.id}@suchana-setu", f"DTSTAMP:{stamp}"]
        if a.due_time:
            hh, mm = a.due_time.split(":")
            lines.append(f"DTSTART;TZID=Asia/Kolkata:{a.due_date.strftime('%Y%m%d')}T{hh}{mm}00")
        else:
            lines += [f"DTSTART;VALUE=DATE:{a.due_date.strftime('%Y%m%d')}", f"DTEND;VALUE=DATE:{(a.due_date + timedelta(days=1)).strftime('%Y%m%d')}"]
        lines += [
            f"SUMMARY:{_ics_escape(a.label)}",
            f"DESCRIPTION:{_ics_escape((n.title or '') + (' Venue: ' + a.venue if a.venue else ''))}",
            "BEGIN:VALARM", "TRIGGER:-P1D", "ACTION:DISPLAY", f"DESCRIPTION:{_ics_escape(a.label)}", "END:VALARM",
            "END:VEVENT",
        ]
    lines.append("END:VCALENDAR")
    return Response("\r\n".join(lines) + "\r\n", media_type="text/calendar", headers={"Content-Disposition": f'attachment; filename="notice-{public_id}.ics"'})


# --- Student inbox (signed link from WhatsApp; no passwords) --------------------------------------


async def _student(s: AsyncSession, token: str) -> Student:
    st = await s.get(Student, verify_student_token(token))
    if st is None:
        raise NotFound("Student not found.", code="student_not_found")
    return st


@router.get("/api/me/inbox")
async def inbox(t: str = Query(..., description="signed token from the WhatsApp link"), s: AsyncSession = Depends(get_session)):
    st = await _student(s, t)
    college = await s.get(College, st.college_id) if st.college_id else None
    notices = []
    if st.college_id:
        rows = (
            await s.scalars(
                select(Notice)
                .where(Notice.college_id == st.college_id, Notice.source == NoticeSource.desk, Notice.status == NoticeStatus.published)
                .options(selectinload(Notice.actions))
                .order_by(Notice.published_at.desc())
                .limit(50)
            )
        ).all()
        tags = set(st.audience_tags or [])
        notices = [n for n in rows if not n.audience_tags or not tags or tags & set(n.audience_tags)]
    today = today_local()
    upcoming = sorted(
        (
            {
                "notice_public_id": n.public_id,
                "notice_title": n.title,
                "label": a.label,
                "due_date": a.due_date.isoformat(),
                "due_date_spoken": speak_date(a.due_date),
                "days_left": (a.due_date - today).days,
                "due_time": a.due_time,
                "verification": a.verification,
            }
            for n in notices
            for a in n.actions
            if a.due_date and a.due_date >= today
        ),
        key=lambda x: x["due_date"],
    )
    return {
        "student": {"preferred_lang": st.preferred_lang, "reminders_enabled": st.reminders_enabled, "college": college.name if college else None},
        "upcoming": upcoming[:20],
        "notices": [notice_summary_json(n) for n in notices],
    }


class Prefs(BaseModel):
    preferred_lang: str | None = None
    reminders_enabled: bool | None = None


@router.patch("/api/me/preferences")
async def preferences(body: Prefs, t: str = Query(...), s: AsyncSession = Depends(get_session)):
    st = await _student(s, t)
    if body.preferred_lang is not None:
        if body.preferred_lang not in LANG_NAMES:
            raise AppError("Language must be en, mr or hi.", code="bad_language")
        st.preferred_lang = body.preferred_lang
    if body.reminders_enabled is not None:
        st.reminders_enabled = body.reminders_enabled
    await s.commit()
    return {"preferred_lang": st.preferred_lang, "reminders_enabled": st.reminders_enabled}


# --- Self-join: a student can start without a college or WhatsApp ----------------------------


class JoinIn(BaseModel):
    lang: str = "en"
    code: str | None = None


@router.post("/api/public/join", status_code=201)
async def join(request: Request, body: JoinIn, s: AsyncSession = Depends(get_session)):
    """Create an anonymous student (no phone, no college) and return their private token."""
    import secrets

    from ..security import sign_student_token

    import hmac

    rate_limit(request, 40)  # generous: many students can share one college Wi-Fi address
    expected = get_settings().demo_access_code
    if expected and not hmac.compare_digest(expected.lower(), (body.code or "").strip().lower()):
        raise AppError("That access code is not right. Ask the Saathi team for the code.", code="access_code_required", status_code=403)
    lang = body.lang if body.lang in LANG_NAMES else "en"
    st = Student(phone_hash="web-" + secrets.token_hex(16), phone_enc="none", preferred_lang=lang, consent_at=utcnow(), reminders_enabled=True)
    s.add(st)
    await s.commit()
    return {"token": sign_student_token(st.id, ttl_days=365)}


# --- Public "check a forward" ---------------------------------------------------------------


@router.post("/api/public/check", status_code=202)
async def check_forward(
    request: Request,
    file: UploadFile = File(...),
    college_code: str | None = Form(None),
    s: AsyncSession = Depends(get_session),
):
    rate_limit(request, get_settings().public_check_per_minute)
    data = await _read_upload(file)
    college_id = None
    if college_code:
        college = await s.scalar(select(College).where(College.code == college_code.strip().upper()))
        if not college:
            raise NotFound("No college with that code.", code="college_not_found")
        college_id = college.id
    key = await get_storage().put(f"checks/{sha256_bytes(data)}", data)
    fc = ForwardCheck(channel="web", college_id=college_id, source_sha256=sha256_bytes(data), result="pending")
    s.add(fc)
    await s.flush()
    await enqueue(s, "check_forward_web", {"check_id": fc.id, "file_key": key}, dedupe_key=f"check:{fc.id}", max_attempts=3)
    await s.commit()
    return {"check_id": fc.id, "status": "pending"}


@router.get("/api/public/check/{check_id}")
async def check_result(check_id: str, s: AsyncSession = Depends(get_session)):
    fc = await s.get(ForwardCheck, check_id)
    if fc is None or fc.channel != "web":
        raise NotFound("Check not found.", code="check_not_found")
    out = {"check_id": fc.id, "status": "pending" if fc.result == "pending" else "done", "result": fc.result, "similarity": fc.similarity}
    if fc.latest_notice_id:
        latest = await load_notice(s, fc.latest_notice_id)
        out["latest"] = notice_public_json(latest) if latest else None
    if fc.result == "official":
        out["message"] = "This is the official, current version of the notice."
    elif fc.result == "superseded":
        out["message"] = "This is an old version. A newer version has been published."
    elif fc.result == "unknown":
        out["message"] = "This notice is not in the college's published list. Confirm it with the college office."
    elif fc.result == "error":
        out["message"] = "We could not read this file. Please try a clearer photo."
    return out


# --- Media (audio only; source scans are staff-only) ------------------------------------------


@router.get("/media/{key:path}")
async def media(key: str):
    if not key.startswith(("audio/", "routes/")):
        raise NotFound("Not found.")
    storage = get_storage()
    if not storage.exists(key):
        raise NotFound("Audio not found.", code="audio_not_found")
    mime = {".ogg": "audio/ogg", ".webm": "audio/webm", ".m4a": "audio/mp4", ".wav": "audio/wav"}.get(Path(key).suffix, "audio/mpeg")
    return FileResponse(storage.local_path(key), media_type=mime, headers={"Cache-Control": "public, max-age=31536000, immutable"})


