"""College desk: publish, review, approve, correct, retry."""

from __future__ import annotations

import datetime as dt

from fastapi import APIRouter, Depends, File, Form, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import get_session, utcnow
from ..errors import AppError, Conflict, NotFound
from ..models import (
    AuditEvent,
    Delivery,
    Job,
    JobStatus,
    Notice,
    NoticeAction,
    NoticeSource,
    NoticeStatus,
    Review,
    StaffRole,
    StaffUser,
    TimetableRow,
    Verification,
)
from ..pipeline.dates import diff_actions, verify_action
from ..pipeline.jobs import enqueue, requeue_dead_for_notice
from ..pipeline.steps import (
    _actions_as_dicts,
    audit,
    emit_notice,
    load_notice,
    today_local,
)
from ..providers.media import sniff_mime
from ..security import current_staff, require_role
from ..serializers import notice_detail_json, notice_summary_json
from ..storage import get_storage, sha256_bytes

router = APIRouter(prefix="/api/notices", tags=["desk"])
review_router = APIRouter(prefix="/api/review-queue", tags=["review"])

LANGS = {"en", "mr", "hi"}


async def _read_upload(file: UploadFile) -> bytes:
    limit = get_settings().max_upload_mb * 1024 * 1024
    data = await file.read(limit + 1)
    if len(data) > limit:
        raise AppError(f"File is larger than {get_settings().max_upload_mb} MB.", code="file_too_large", status_code=413)
    if not data:
        raise AppError("The file is empty.", code="empty_file")
    if sniff_mime(data) is None:
        raise AppError("Please upload a PDF, JPG, PNG or WEBP file.", code="unsupported_file", status_code=415)
    return data


async def _get_owned(s: AsyncSession, notice_id: str, staff: StaffUser) -> Notice:
    n = await load_notice(s, notice_id)
    if n is None or n.college_id != staff.college_id:
        raise NotFound("Notice not found.", code="notice_not_found")
    return n


async def create_notice_from_upload(
    s: AsyncSession,
    staff: StaffUser,
    data: bytes,
    filename: str | None,
    language_hint: str | None,
    audience_tags: list[str],
    supersedes: Notice | None,
) -> tuple[Notice, bool]:
    sha = sha256_bytes(data)
    if supersedes is None:
        dup = await s.scalar(
            select(Notice).where(
                Notice.college_id == staff.college_id,
                Notice.source_sha256 == sha,
                Notice.source == NoticeSource.desk,
                Notice.status.not_in([NoticeStatus.rejected, NoticeStatus.failed]),
            )
        )
        if dup:
            return dup, True
    mime = sniff_mime(data)
    ext = {"application/pdf": ".pdf", "image/png": ".png", "image/webp": ".webp"}.get(mime, ".jpg")
    key = await get_storage().put(f"sources/{sha}{ext}", data)
    n = Notice(
        college_id=staff.college_id,
        source=NoticeSource.desk,
        status=NoticeStatus.queued,
        stage="queued",
        source_file_key=key,
        source_mime=mime,
        source_sha256=sha,
        source_filename=(filename or "")[:200] or None,
        language_hint=language_hint,
        audience_tags=audience_tags or None,
        created_by_id=staff.id,
    )
    if supersedes is not None:
        n.series_id = supersedes.series_id
        n.version = supersedes.version + 1
        n.supersedes_id = supersedes.id
        n.audience_tags = n.audience_tags or supersedes.audience_tags
    s.add(n)
    await s.flush()
    await enqueue(s, "process_notice", {"notice_id": n.id}, dedupe_key=f"process:{n.id}", notice_id=n.id)
    audit(s, "notice.uploaded", notice=n, actor=f"staff:{staff.id}", filename=filename, version=n.version)
    await s.commit()
    emit_notice(n)
    return n, False


def _parse_tags(raw: str | None) -> list[str]:
    return [t.strip().upper() for t in (raw or "").split(",") if t.strip()][:20]


@router.post("", status_code=202)
async def upload_notice(
    file: UploadFile = File(...),
    language_hint: str | None = Form(None),
    audience_tags: str | None = Form(None, description="Comma separated, e.g. FY-BCOM,SY-BCOM. Empty = all students"),
    supersedes_id: str | None = Form(None),
    staff: StaffUser = Depends(require_role(StaffRole.admin, StaffRole.staff)),
    s: AsyncSession = Depends(get_session),
):
    if language_hint and language_hint not in LANGS:
        raise AppError("language_hint must be en, mr or hi.", code="bad_language")
    data = await _read_upload(file)
    old = None
    if supersedes_id:
        old = await _get_owned(s, supersedes_id, staff)
        if old.status not in (NoticeStatus.published, NoticeStatus.needs_review):
            raise Conflict("Only a published notice can be corrected with a new version.", code="cannot_supersede")
        newer = await s.scalar(select(Notice.id).where(Notice.supersedes_id == old.id, Notice.status.not_in([NoticeStatus.rejected, NoticeStatus.failed])))
        if newer:
            raise Conflict("A newer version of this notice already exists.", code="already_superseded")
    n, duplicate = await create_notice_from_upload(s, staff, data, file.filename, language_hint, _parse_tags(audience_tags), old)
    return {"notice": notice_summary_json(n), "duplicate": duplicate}


@router.get("")
async def list_notices(
    status: str | None = None,
    q: str | None = Query(None, max_length=100),
    limit: int = Query(50, le=200),
    staff: StaffUser = Depends(current_staff),
    s: AsyncSession = Depends(get_session),
):
    stmt = (
        select(Notice)
        .where(Notice.college_id == staff.college_id, Notice.source == NoticeSource.desk)
        .options(selectinload(Notice.actions))
        .order_by(Notice.created_at.desc())
        .limit(limit)
    )
    if status:
        stmt = stmt.where(Notice.status == status)
    if q:
        stmt = stmt.where(or_(Notice.title.ilike(f"%{q}%"), Notice.issuer.ilike(f"%{q}%")))
    return {"notices": [notice_summary_json(n) for n in (await s.scalars(stmt)).all()]}


@router.get("/{notice_id}")
async def get_notice(notice_id: str, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    n = await _get_owned(s, notice_id, staff)
    data = notice_detail_json(n)
    events = (await s.scalars(select(AuditEvent).where(AuditEvent.notice_id == n.id).order_by(AuditEvent.created_at))).all()
    data["timeline"] = [{"event": e.event, "actor": e.actor, "at": e.created_at.isoformat(), "detail": e.detail} for e in events]
    counts = (await s.execute(select(Delivery.status, func.count()).where(Delivery.notice_id == n.id).group_by(Delivery.status))).all()
    data["deliveries"] = {k: v for k, v in counts}
    return data


@router.get("/{notice_id}/source")
async def get_source(notice_id: str, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    n = await _get_owned(s, notice_id, staff)
    if not n.source_file_key:
        raise NotFound("No source file.", code="no_source")
    data = await get_storage().get(n.source_file_key)
    return Response(data, media_type=n.source_mime or "application/octet-stream", headers={"Cache-Control": "private, max-age=3600"})


# --- Review & edit ----------------------------------------------------------------------


class ActionIn(BaseModel):
    id: str | None = None
    kind: str = "deadline"
    label: str = Field(min_length=1, max_length=300)
    due_date: dt.date | None = None
    due_time: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    amount: str | None = Field(None, max_length=40)
    venue: str | None = Field(None, max_length=200)


class RowIn(BaseModel):
    day: str | None = None
    date: dt.date | None = None
    start: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    end: str | None = Field(None, pattern=r"^([01]\d|2[0-3]):[0-5]\d$")
    subject: str | None = None
    room: str | None = None
    faculty: str | None = None
    batch: str | None = None


class NoticeEdit(BaseModel):
    title: str | None = Field(None, max_length=300)
    issuer: str | None = Field(None, max_length=200)
    audience: str | None = Field(None, max_length=200)
    audience_tags: list[str] | None = None
    summaries: dict[str, str] | None = None
    body_markdown: str | None = None
    actions: list[ActionIn] | None = None
    timetable: list[RowIn] | None = None

    @field_validator("summaries")
    @classmethod
    def _langs(cls, v):
        if v and not set(v) <= LANGS:
            raise ValueError("summaries keys must be en, mr or hi")
        if v and any(len(x) > 1200 for x in v.values()):
            raise ValueError("summaries must be under 1200 characters")
        return v


EDITABLE = (NoticeStatus.needs_review,)


@router.patch("/{notice_id}")
async def edit_notice(
    notice_id: str,
    body: NoticeEdit,
    staff: StaffUser = Depends(current_staff),
    s: AsyncSession = Depends(get_session),
):
    n = await _get_owned(s, notice_id, staff)
    if n.status not in EDITABLE:
        raise Conflict("Only notices waiting for review can be edited. Upload a new version to correct a published notice.", code="not_editable")
    changed: list[str] = []
    for field in ("title", "issuer", "audience", "body_markdown"):
        val = getattr(body, field)
        if val is not None and val != getattr(n, field):
            setattr(n, field, val)
            changed.append(field)
    if body.audience_tags is not None:
        n.audience_tags = [t.strip().upper() for t in body.audience_tags if t.strip()] or None
        changed.append("audience_tags")
    if body.summaries is not None:
        n.summaries = {**(n.summaries or {}), **body.summaries}
        n.renditions.clear()  # text changed: audio must be regenerated at publish
        changed.append("summaries")
    if body.actions is not None:
        old = {a.id: a for a in n.actions}
        n.actions.clear()
        ref = today_local()
        for i, a in enumerate(body.actions):
            prev = old.get(a.id) if a.id else None
            same = prev is not None and (prev.due_date, prev.due_time, prev.amount) == (a.due_date, a.due_time, a.amount)
            if same:
                status, reasons, quote = prev.verification, prev.verify_reasons, prev.source_quote
            else:
                # A human typed or changed this value: re-check against the scan, then trust the human.
                res = verify_action(
                    due_date=a.due_date.isoformat() if a.due_date else None,
                    due_time=a.due_time,
                    amount=a.amount,
                    source_quote=prev.source_quote if prev else None,
                    ocr_text=n.ocr_text or "",
                    ref=ref,
                )
                status = Verification.human_verified
                reasons = ([f"Entered by {staff.name}."] + [r for r in res.reasons if "past" in r or "weekday" in r.lower() or "says" in r])
                quote = prev.source_quote if prev else None
            n.actions.append(
                NoticeAction(
                    sort=i, kind=a.kind, label=a.label, due_date=a.due_date, due_time=a.due_time, amount=a.amount,
                    venue=a.venue, source_quote=quote, verification=status, verify_reasons=reasons or None,
                )
            )
        changed.append("actions")
    if body.timetable is not None:
        n.timetable.clear()
        for i, r in enumerate(body.timetable):
            n.timetable.append(TimetableRow(sort=i, day=r.day, on_date=r.date, start=r.start, end=r.end, subject=r.subject, room=r.room, faculty=r.faculty, batch=r.batch))
        changed.append("timetable")
    if n.supersedes_id and "actions" in changed:
        prev_notice = await load_notice(s, n.supersedes_id)
        if prev_notice:
            n.changes_from_previous = diff_actions(_actions_as_dicts(prev_notice.actions), _actions_as_dicts(n.actions)) or None
    audit(s, "notice.edited", notice=n, actor=f"staff:{staff.id}", fields=changed)
    await s.commit()
    n = await load_notice(s, n.id)
    emit_notice(n)
    return notice_detail_json(n)


class ApproveIn(BaseModel):
    confirmed_action_ids: list[str] = Field(default_factory=list, description="needs_check dates the reviewer confirmed against the original")
    minutes: float | None = Field(None, ge=0, le=240)
    note: str | None = Field(None, max_length=1000)


@router.post("/{notice_id}/approve")
async def approve_notice(
    notice_id: str,
    body: ApproveIn,
    staff: StaffUser = Depends(require_role(StaffRole.admin, StaffRole.staff)),
    s: AsyncSession = Depends(get_session),
):
    n = await _get_owned(s, notice_id, staff)
    if n.status == NoticeStatus.approved or n.status == NoticeStatus.published:
        return {"notice": notice_summary_json(n), "already": True}
    if n.status != NoticeStatus.needs_review:
        raise Conflict(f"This notice is {n.status} and cannot be approved.", code="not_reviewable")
    if n.source != NoticeSource.desk:
        raise Conflict("Forwarded copies cannot be published; upload the official file instead.", code="forward_not_publishable")
    confirmed = set(body.confirmed_action_ids)
    unresolved = []
    for a in n.actions:
        if a.verification == Verification.needs_check:
            if a.id in confirmed:
                a.verification = Verification.human_verified
                a.verify_reasons = (a.verify_reasons or []) + [f"Confirmed against the original by {staff.name}."]
            else:
                unresolved.append({"id": a.id, "label": a.label, "reasons": a.verify_reasons or []})
    if unresolved:
        await s.rollback()
        raise Conflict(
            "Some dates could not be confirmed automatically. Check them against the original and confirm or correct each one.",
            code="unverified_dates",
            detail=unresolved,
        )
    n.status = NoticeStatus.approved
    n.stage = "voice"
    n.approved_by_id = staff.id
    n.approved_at = utcnow()
    minutes = body.minutes
    if minutes is None:
        minutes = min(30.0, max(0.5, (utcnow() - n.updated_at).total_seconds() / 60))
    s.add(Review(notice_id=n.id, reviewer_id=staff.id, decision="approved", minutes=round(minutes, 1), note=body.note))
    await enqueue(s, "publish_notice", {"notice_id": n.id}, dedupe_key=f"publish:{n.id}", notice_id=n.id)
    audit(s, "notice.approved", notice=n, actor=f"staff:{staff.id}", confirmed=len(confirmed))
    await s.commit()
    emit_notice(n)
    return {"notice": notice_summary_json(n), "already": False}


class RejectIn(BaseModel):
    note: str = Field(min_length=3, max_length=1000)
    minutes: float | None = Field(None, ge=0, le=240)


@router.post("/{notice_id}/reject")
async def reject_notice(notice_id: str, body: RejectIn, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    n = await _get_owned(s, notice_id, staff)
    if n.status != NoticeStatus.needs_review:
        raise Conflict("Only notices waiting for review can be rejected.", code="not_reviewable")
    n.status = NoticeStatus.rejected
    s.add(Review(notice_id=n.id, reviewer_id=staff.id, decision="rejected", minutes=body.minutes or 0, note=body.note))
    audit(s, "notice.rejected", notice=n, actor=f"staff:{staff.id}", note=body.note)
    await s.commit()
    emit_notice(n)
    return {"notice": notice_summary_json(n)}


@router.post("/{notice_id}/retry")
async def retry_notice(notice_id: str, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    n = await _get_owned(s, notice_id, staff)
    if n.status != NoticeStatus.failed:
        raise Conflict("Only failed notices can be retried.", code="not_failed")
    revived = await requeue_dead_for_notice(s, n.id)
    if not revived:
        step = "publish_notice" if n.approved_at else "process_notice"
        await enqueue(s, step, {"notice_id": n.id}, notice_id=n.id)
    n.status = NoticeStatus.approved if n.approved_at else NoticeStatus.queued
    n.error_code = n.error_message = None
    n.stage = "queued"
    audit(s, "notice.retried", notice=n, actor=f"staff:{staff.id}")
    await s.commit()
    emit_notice(n)
    return {"notice": notice_summary_json(n)}


# --- Review queue (staff + Rotaract verifiers) --------------------------------------------------


@review_router.get("")
async def review_queue(staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    rows = (
        await s.scalars(
            select(Notice)
            .where(Notice.college_id == staff.college_id, Notice.status == NoticeStatus.needs_review)
            .options(selectinload(Notice.actions))
            .order_by(Notice.created_at)
        )
    ).all()
    out = []
    for n in rows:
        item = notice_summary_json(n)
        item["unverified_dates"] = sum(1 for a in n.actions if a.verification == Verification.needs_check)
        item["flags"] = (n.flags or [])[:3]
        out.append(item)
    return {"queue": out}


@router.get("/{notice_id}/jobs")
async def notice_jobs(notice_id: str, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    await _get_owned(s, notice_id, staff)
    jobs = (await s.scalars(select(Job).where(Job.notice_id == notice_id).order_by(Job.created_at))).all()
    return {
        "jobs": [
            {"type": j.type, "status": j.status, "attempts": j.attempts, "last_error": j.last_error, "run_after": j.run_after.isoformat()}
            for j in jobs
        ],
        "dead": sum(1 for j in jobs if j.status == JobStatus.dead),
    }

