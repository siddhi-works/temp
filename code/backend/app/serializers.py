"""Turn database rows into API JSON. One place, so the frontend gets a stable shape."""

from __future__ import annotations

from .config import get_settings
from .models import Notice, NoticeAction, Rendition, TimetableRow
from .pipeline.dates import speak_date


def media_url(key: str | None) -> str | None:
    if not key:
        return None
    return f"{get_settings().public_base_url.rstrip('/')}/media/{key}"


def action_json(a: NoticeAction) -> dict:
    return {
        "id": a.id,
        "kind": a.kind,
        "label": a.label,
        "due_date": a.due_date.isoformat() if a.due_date else None,
        "due_date_spoken": speak_date(a.due_date) if a.due_date else None,
        "due_time": a.due_time,
        "amount": a.amount,
        "venue": a.venue,
        "source_quote": a.source_quote,
        "verification": a.verification,
        "verify_reasons": a.verify_reasons or [],
    }


def row_json(r: TimetableRow) -> dict:
    return {
        "day": r.day,
        "date": r.on_date.isoformat() if r.on_date else None,
        "start": r.start,
        "end": r.end,
        "subject": r.subject,
        "room": r.room,
        "faculty": r.faculty,
        "batch": r.batch,
    }


def rendition_json(r: Rendition) -> dict:
    return {
        "lang": r.lang,
        "text": r.text,
        "audio_url": media_url(r.audio_key),
        "engine": r.tts_engine,
        "error": r.error,
    }


def notice_summary_json(n: Notice) -> dict:
    next_due = min((a.due_date for a in n.actions if a.due_date), default=None) if "actions" in n.__dict__ else None
    return {
        "id": n.id,
        "public_id": n.public_id,
        "version": n.version,
        "status": n.status,
        "stage": n.stage,
        "source": n.source,
        "title": n.title,
        "issuer": n.issuer,
        "category": n.category,
        "confidence": n.confidence,
        "next_due": next_due.isoformat() if next_due else None,
        "error_message": n.error_message,
        "created_at": n.created_at.isoformat(),
        "published_at": n.published_at.isoformat() if n.published_at else None,
    }


def notice_public_json(n: Notice) -> dict:
    """What students see. No OCR internals, no reviewer notes."""
    approver = n.approved_by.office or n.approved_by.name if n.approved_by else None
    return {
        "public_id": n.public_id,
        "version": n.version,
        "status": n.status,
        "title": n.title,
        "issuer": n.issuer,
        "college": n.college.name if n.college else None,
        "category": n.category,
        "issue_date": n.issue_date.isoformat() if n.issue_date else None,
        "audience": n.audience,
        "summaries": n.summaries or {},
        "actions": [action_json(a) for a in n.actions],
        "timetable": [row_json(r) for r in n.timetable],
        "renditions": [rendition_json(r) for r in n.renditions],
        "body_markdown": n.body_markdown,
        "changes_from_previous": n.changes_from_previous or [],
        "approved_by": approver,
        "approved_at": n.approved_at.isoformat() if n.approved_at else None,
        "published_at": n.published_at.isoformat() if n.published_at else None,
        "superseded_at": n.superseded_at.isoformat() if n.superseded_at else None,
        "calendar_url": f"{get_settings().public_base_url.rstrip('/')}/api/public/notices/{n.public_id}/calendar.ics",
        "page_url": f"{get_settings().public_base_url.rstrip('/')}/n/{n.public_id}",
    }


def notice_detail_json(n: Notice) -> dict:
    """What staff and verifiers see during review."""
    data = notice_public_json(n)
    data.update(
        {
            "id": n.id,
            "series_id": n.series_id,
            "supersedes_id": n.supersedes_id,
            "stage": n.stage,
            "source": n.source,
            "audience_tags": n.audience_tags or [],
            "language_hint": n.language_hint,
            "page_count": n.page_count,
            "ocr_text": n.ocr_text,
            "ocr_engine": n.ocr_engine,
            "extraction_engine": n.extraction_engine,
            "confidence": n.confidence,
            "flags": n.flags or [],
            "error_code": n.error_code,
            "error_message": n.error_message,
            "source_url": f"/api/notices/{n.id}/source",
            "created_at": n.created_at.isoformat(),
        }
    )
    return data
