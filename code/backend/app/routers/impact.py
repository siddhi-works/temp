"""Accessibility log and impact numbers (NAAC 7.1.7 evidence; Rotaract service minutes).

Every number is computed from real rows. During the hackathon, the frontend must
label these as test-run data.
"""

from __future__ import annotations

import csv
import io
import statistics
from datetime import timedelta

from fastapi import APIRouter, Depends, Query
from fastapi.responses import StreamingResponse
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session, utcnow
from ..models import (
    Delivery,
    ForwardCheck,
    Notice,
    NoticeAction,
    NoticeSource,
    NoticeStatus,
    ProviderUsage,
    Reminder,
    Rendition,
    Review,
    StaffUser,
    Student,
    Verification,
)
from ..providers.base import breaker_states
from ..security import current_staff

router = APIRouter(prefix="/api/impact", tags=["impact"])


@router.get("")
async def impact(days: int = Query(30, ge=1, le=365), staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    cid = staff.college_id
    since = utcnow() - timedelta(days=days)

    notices = (await s.scalars(select(Notice).where(Notice.college_id == cid, Notice.source == NoticeSource.desk, Notice.created_at >= since))).all()
    published = [n for n in notices if n.published_at]
    minutes_to_accessible = [(n.published_at - n.created_at).total_seconds() / 60 for n in published]

    status_counts = dict((await s.execute(
        select(Delivery.status, func.count()).join(Notice, Notice.id == Delivery.notice_id)
        .where(Notice.college_id == cid, Delivery.created_at >= since, Delivery.kind != "clash").group_by(Delivery.status)
    )).all())
    reached = sum(status_counts.get(k, 0) for k in ("sent", "delivered", "read"))

    actions = (await s.execute(
        select(NoticeAction.verification, func.count()).join(Notice, Notice.id == NoticeAction.notice_id)
        .where(Notice.college_id == cid, Notice.created_at >= since, NoticeAction.due_date.is_not(None)).group_by(NoticeAction.verification)
    )).all()
    action_counts = dict(actions)

    reviews = (await s.execute(
        select(StaffUser.role, StaffUser.rotaract_club, func.sum(Review.minutes), func.count())
        .join(StaffUser, StaffUser.id == Review.reviewer_id).join(Notice, Notice.id == Review.notice_id)
        .where(Notice.college_id == cid, Review.created_at >= since).group_by(StaffUser.role, StaffUser.rotaract_club)
    )).all()

    checks = dict((await s.execute(
        select(ForwardCheck.result, func.count()).where(ForwardCheck.college_id == cid, ForwardCheck.created_at >= since).group_by(ForwardCheck.result)
    )).all())

    reminders_sent = await s.scalar(
        select(func.count(Reminder.id)).join(Notice, Notice.id == Reminder.notice_id).where(Notice.college_id == cid, Reminder.status == "sent")
    )
    students = await s.scalar(select(func.count(Student.id)).where(Student.college_id == cid, Student.opted_out_at.is_(None)))

    return {
        "period_days": days,
        "data_note": "Computed from this system's own records.",
        "notices": {
            "uploaded": len(notices),
            "published": len(published),
            "awaiting_review": sum(1 for n in notices if n.status == NoticeStatus.needs_review),
            "failed": sum(1 for n in notices if n.status == NoticeStatus.failed),
            "corrected_versions": sum(1 for n in notices if n.version > 1),
            "median_minutes_upload_to_accessible": round(statistics.median(minutes_to_accessible), 1) if minutes_to_accessible else None,
        },
        "dates": {
            "auto_verified": action_counts.get(Verification.verified, 0),
            "confirmed_by_humans": action_counts.get(Verification.human_verified, 0),
            "still_flagged": action_counts.get(Verification.needs_check, 0),
        },
        "students": {"active": students or 0},
        "deliveries": {"reached": reached, "read": status_counts.get("read", 0), "by_status": status_counts},
        "reminders_sent": reminders_sent or 0,
        "forward_checks": checks,
        "review_minutes": [
            {"role": role, "rotaract_club": club, "minutes": round(m or 0, 1), "reviews": c} for role, club, m, c in reviews
        ],
    }


@router.get("/log.csv")
async def accessibility_log(staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    """One row per notice: the evidence file for NAAC metric 7.1.7 and the disability cell."""
    notices = (await s.scalars(select(Notice).where(Notice.college_id == staff.college_id, Notice.source == NoticeSource.desk).order_by(Notice.created_at))).all()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["notice_id", "title", "version", "status", "uploaded_at", "published_at", "minutes_to_accessible", "languages_with_audio", "students_reached", "dates_auto_verified", "dates_human_confirmed"])
    for n in notices:
        langs = sorted((await s.scalars(select(Rendition.lang).where(Rendition.notice_id == n.id, Rendition.audio_key.is_not(None)))).all())
        reached = await s.scalar(select(func.count(Delivery.id)).where(Delivery.notice_id == n.id, Delivery.kind != "clash", Delivery.status.in_(["sent", "delivered", "read"])))
        acts = dict((await s.execute(select(NoticeAction.verification, func.count()).where(NoticeAction.notice_id == n.id).group_by(NoticeAction.verification))).all())
        mins = round((n.published_at - n.created_at).total_seconds() / 60, 1) if n.published_at else ""
        w.writerow([n.public_id, n.title, n.version, n.status, n.created_at.isoformat(), n.published_at.isoformat() if n.published_at else "", mins, " ".join(langs), reached or 0, acts.get("verified", 0), acts.get("human_verified", 0)])
    buf.seek(0)
    return StreamingResponse(iter([buf.getvalue()]), media_type="text/csv", headers={"Content-Disposition": 'attachment; filename="accessibility-log.csv"'})


@router.get("/usage")
async def usage(staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    """API spend and health, so the team sees the Sarvam budget before the demo, not during it."""
    rows = (await s.execute(
        select(ProviderUsage.provider, ProviderUsage.operation, func.count())
        .group_by(ProviderUsage.provider, ProviderUsage.operation)
    )).all()
    spent = await s.scalar(select(func.coalesce(func.sum(ProviderUsage.cost_paise), 0)).where(ProviderUsage.provider == "sarvam"))
    failures = dict((await s.execute(select(ProviderUsage.provider, func.count()).where(ProviderUsage.success.is_(False)).group_by(ProviderUsage.provider))).all())
    cap = get_settings().sarvam_budget_paise
    return {
        "sarvam": {"spent_rupees": round((spent or 0) / 100, 2), "cap_rupees": cap / 100, "remaining_rupees": round((cap - (spent or 0)) / 100, 2)},
        "calls": [{"provider": p, "operation": o, "count": c} for p, o, c in rows if o not in ("reserve", "refund")],
        "failures": failures,
        "circuits": breaker_states(),
    }

