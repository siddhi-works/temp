"""Database tables.

Design notes
- A notice belongs to a *series*; every correction is a new version in the same
  series, and the old version is marked superseded. This powers "check a
  forward" (is this photo the latest version?) and "what changed" alerts.
- Every paid or slow pipeline step stores its output on the notice, so a retry
  resumes from the failed step instead of paying for OCR again.
- Phone numbers are stored encrypted, with an HMAC hash for lookups.
"""

from __future__ import annotations

from datetime import date, datetime
from enum import StrEnum

from sqlalchemy import (
    JSON,
    Boolean,
    Date,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from .db import Base, UTCDateTime, new_id, new_public_id, utcnow


class NoticeStatus(StrEnum):
    queued = "queued"
    processing = "processing"  # OCR, extraction, verification
    needs_review = "needs_review"
    approved = "approved"  # approved, audio + delivery in progress
    published = "published"
    rejected = "rejected"
    failed = "failed"
    superseded = "superseded"


class NoticeSource(StrEnum):
    desk = "desk"
    forward = "forward"  # a student's forwarded photo, never published to others


class Verification(StrEnum):
    verified = "verified"  # found verbatim in the source and parsed consistently
    needs_check = "needs_check"
    human_verified = "human_verified"


class StaffRole(StrEnum):
    admin = "admin"
    staff = "staff"
    verifier = "verifier"  # e.g. trained Rotaract volunteer


class JobStatus(StrEnum):
    queued = "queued"
    running = "running"
    done = "done"
    dead = "dead"  # exhausted retries; needs a human


class DeliveryStatus(StrEnum):
    queued = "queued"
    sent = "sent"
    delivered = "delivered"
    read = "read"
    failed = "failed"
    deferred_window = "deferred_window"  # outside 24h window and no template configured
    skipped = "skipped"  # opted out


class College(Base):
    __tablename__ = "college"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    code: Mapped[str] = mapped_column(String(16), unique=True)  # students type "JOIN <code>"
    name: Mapped[str] = mapped_column(String(200))
    district: Mapped[str | None] = mapped_column(String(100))
    disability_cell_contact: Mapped[str | None] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class StaffUser(Base):
    __tablename__ = "staff_user"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    college_id: Mapped[str] = mapped_column(ForeignKey("college.id"))
    name: Mapped[str] = mapped_column(String(120))
    role: Mapped[str] = mapped_column(String(16), default=StaffRole.staff)
    office: Mapped[str | None] = mapped_column(String(120))  # "Exam Section" -> shown on verified-by line
    rotaract_club: Mapped[str | None] = mapped_column(String(120))
    token_hash: Mapped[str] = mapped_column(String(64), unique=True)
    active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    college: Mapped[College] = relationship(lazy="joined")


class Notice(Base):
    __tablename__ = "notice"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    public_id: Mapped[str] = mapped_column(String(16), unique=True, default=new_public_id)
    college_id: Mapped[str | None] = mapped_column(ForeignKey("college.id"), index=True)
    series_id: Mapped[str] = mapped_column(String(32), index=True, default=new_id)
    version: Mapped[int] = mapped_column(Integer, default=1)
    supersedes_id: Mapped[str | None] = mapped_column(ForeignKey("notice.id"))
    source: Mapped[str] = mapped_column(String(16), default=NoticeSource.desk)
    status: Mapped[str] = mapped_column(String(20), default=NoticeStatus.queued, index=True)
    stage: Mapped[str | None] = mapped_column(String(32))  # fine-grained progress for the UI

    # Source document
    source_file_key: Mapped[str | None] = mapped_column(String(300))
    source_mime: Mapped[str | None] = mapped_column(String(60))
    source_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    source_filename: Mapped[str | None] = mapped_column(String(200))
    page_count: Mapped[int | None] = mapped_column(Integer)
    language_hint: Mapped[str | None] = mapped_column(String(8))  # en / mr / hi

    # Pipeline outputs (checkpoints)
    ocr_text: Mapped[str | None] = mapped_column(Text)
    ocr_engine: Mapped[str | None] = mapped_column(String(40))
    extraction: Mapped[dict | None] = mapped_column(JSON)
    extraction_engine: Mapped[str | None] = mapped_column(String(60))
    summaries: Mapped[dict | None] = mapped_column(JSON)  # {"en": "...", "mr": "...", "hi": "..."}
    flags: Mapped[list | None] = mapped_column(JSON)  # human-readable reasons to look closely
    confidence: Mapped[float | None] = mapped_column(Float)
    changes_from_previous: Mapped[list | None] = mapped_column(JSON)

    # Display fields (editable by staff during review)
    title: Mapped[str | None] = mapped_column(String(300))
    issuer: Mapped[str | None] = mapped_column(String(200))
    category: Mapped[str | None] = mapped_column(String(40))
    issue_date: Mapped[date | None] = mapped_column(Date)
    audience: Mapped[str | None] = mapped_column(String(200))
    audience_tags: Mapped[list | None] = mapped_column(JSON)  # e.g. ["FY-BCOM"]; empty = everyone
    body_markdown: Mapped[str | None] = mapped_column(Text)

    error_code: Mapped[str | None] = mapped_column(String(40))
    error_message: Mapped[str | None] = mapped_column(Text)

    created_by_id: Mapped[str | None] = mapped_column(ForeignKey("staff_user.id"))
    forwarded_by_student_id: Mapped[str | None] = mapped_column(ForeignKey("student.id"))
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("staff_user.id"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    superseded_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)

    actions: Mapped[list[NoticeAction]] = relationship(
        back_populates="notice", cascade="all, delete-orphan", order_by="NoticeAction.sort"
    )
    timetable: Mapped[list[TimetableRow]] = relationship(
        back_populates="notice", cascade="all, delete-orphan", order_by="TimetableRow.sort"
    )
    renditions: Mapped[list[Rendition]] = relationship(back_populates="notice", cascade="all, delete-orphan")
    approved_by: Mapped[StaffUser | None] = relationship(foreign_keys=[approved_by_id])
    college: Mapped[College | None] = relationship()


class NoticeAction(Base):
    """Something a student must do or attend: a deadline, exam, fee, venue change."""

    __tablename__ = "notice_action"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    notice_id: Mapped[str] = mapped_column(ForeignKey("notice.id", ondelete="CASCADE"), index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    kind: Mapped[str] = mapped_column(String(24), default="deadline")
    label: Mapped[str] = mapped_column(String(300))
    due_date: Mapped[date | None] = mapped_column(Date)
    due_time: Mapped[str | None] = mapped_column(String(5))  # "HH:MM", local time
    amount: Mapped[str | None] = mapped_column(String(40))
    venue: Mapped[str | None] = mapped_column(String(200))
    source_quote: Mapped[str | None] = mapped_column(Text)
    verification: Mapped[str] = mapped_column(String(16), default=Verification.needs_check)
    verify_reasons: Mapped[list | None] = mapped_column(JSON)

    notice: Mapped[Notice] = relationship(back_populates="actions")


class TimetableRow(Base):
    __tablename__ = "timetable_row"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    notice_id: Mapped[str] = mapped_column(ForeignKey("notice.id", ondelete="CASCADE"), index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    day: Mapped[str | None] = mapped_column(String(20))
    on_date: Mapped[date | None] = mapped_column(Date)
    start: Mapped[str | None] = mapped_column(String(5))
    end: Mapped[str | None] = mapped_column(String(5))
    subject: Mapped[str | None] = mapped_column(String(200))
    room: Mapped[str | None] = mapped_column(String(60))
    faculty: Mapped[str | None] = mapped_column(String(120))
    batch: Mapped[str | None] = mapped_column(String(60))

    notice: Mapped[Notice] = relationship(back_populates="timetable")


class Rendition(Base):
    """Spoken summary of a notice in one language."""

    __tablename__ = "rendition"
    __table_args__ = (UniqueConstraint("notice_id", "lang"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    notice_id: Mapped[str] = mapped_column(ForeignKey("notice.id", ondelete="CASCADE"))
    lang: Mapped[str] = mapped_column(String(8))
    text: Mapped[str] = mapped_column(Text)
    audio_key: Mapped[str | None] = mapped_column(String(300))  # mp3 for the web
    voice_note_key: Mapped[str | None] = mapped_column(String(300))  # ogg/opus for WhatsApp
    tts_engine: Mapped[str | None] = mapped_column(String(40))
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    notice: Mapped[Notice] = relationship(back_populates="renditions")


class Student(Base):
    __tablename__ = "student"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    college_id: Mapped[str | None] = mapped_column(ForeignKey("college.id"), index=True)
    phone_hash: Mapped[str] = mapped_column(String(64), unique=True)
    phone_enc: Mapped[str] = mapped_column(Text)
    name: Mapped[str | None] = mapped_column(String(120))
    preferred_lang: Mapped[str] = mapped_column(String(8), default="en")
    audience_tags: Mapped[list | None] = mapped_column(JSON)
    reminders_enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    catch_until: Mapped[datetime | None] = mapped_column(UTCDateTime)  # armed by CATCH: next message is a deadline
    buzz_prefs: Mapped[dict | None] = mapped_column(JSON)  # per message type vibration overrides
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    opted_out_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    last_inbound_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Delivery(Base):
    __tablename__ = "delivery"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    dedupe_key: Mapped[str] = mapped_column(String(200), unique=True)
    notice_id: Mapped[str] = mapped_column(ForeignKey("notice.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id"), index=True)
    kind: Mapped[str] = mapped_column(String(16), default="notice")  # notice | update | reminder
    status: Mapped[str] = mapped_column(String(20), default=DeliveryStatus.queued)
    wa_message_ids: Mapped[list | None] = mapped_column(JSON)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class Reminder(Base):
    """kind: deadline (from a notice), exam_eve (night-before briefing), personal (caught in class)."""

    __tablename__ = "reminder"
    __table_args__ = (UniqueConstraint("kind", "action_id", "personal_id", "student_id", "offset_days"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    kind: Mapped[str] = mapped_column(String(16), default="deadline")
    action_id: Mapped[str | None] = mapped_column(ForeignKey("notice_action.id", ondelete="CASCADE"))
    personal_id: Mapped[str | None] = mapped_column(ForeignKey("personal_deadline.id", ondelete="CASCADE"))
    notice_id: Mapped[str | None] = mapped_column(ForeignKey("notice.id"), index=True)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id"))
    offset_days: Mapped[int] = mapped_column(Integer)
    send_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    status: Mapped[str] = mapped_column(String(16), default="pending")  # pending|enqueued|sent|cancelled|failed


class StudentSlot(Base):
    """One recurring weekly class in a student's own timetable."""

    __tablename__ = "student_slot"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"), index=True)
    weekday: Mapped[int] = mapped_column(Integer)  # 0 = Monday
    start: Mapped[str] = mapped_column(String(5))
    end: Mapped[str] = mapped_column(String(5))
    subject: Mapped[str] = mapped_column(String(200))
    room: Mapped[str | None] = mapped_column(String(60))
    source_notice_id: Mapped[str | None] = mapped_column(String(32))


class PersonalDeadline(Base):
    """A deadline the student heard in class and saved by voice. Confirmed by read-back before it counts."""

    __tablename__ = "personal_deadline"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"), index=True)
    label: Mapped[str] = mapped_column(String(300))
    due_date: Mapped[date | None] = mapped_column(Date)
    due_time: Mapped[str | None] = mapped_column(String(5))
    transcript: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(String(10), default="voice")  # voice | text
    status: Mapped[str] = mapped_column(String(12), default="pending")  # pending | confirmed | cancelled
    needs_date: Mapped[bool] = mapped_column(Boolean, default=False)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime)


class Snap(Base):
    """A photographed whiteboard, stored as searchable text: the student's lecture memory."""

    __tablename__ = "snap"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"), index=True)
    subject: Mapped[str | None] = mapped_column(String(200))
    room: Mapped[str | None] = mapped_column(String(60))
    source_key: Mapped[str | None] = mapped_column(String(300))
    ocr_text: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    ocr_engine: Mapped[str | None] = mapped_column(String(40))
    status: Mapped[str] = mapped_column(String(12), default="processing")  # processing | ready | failed
    error: Mapped[str | None] = mapped_column(Text)
    captured_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class Route(Base):
    """A walking route recorded by a volunteer, played to a blind student from a QR code."""

    __tablename__ = "route"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    public_id: Mapped[str] = mapped_column(String(16), unique=True, default=new_public_id)
    college_id: Mapped[str] = mapped_column(ForeignKey("college.id"), index=True)
    title: Mapped[str] = mapped_column(String(200))
    from_place: Mapped[str] = mapped_column(String(120))
    to_place: Mapped[str] = mapped_column(String(120))
    status: Mapped[str] = mapped_column(String(12), default="draft")  # draft | published
    recorded_by_id: Mapped[str | None] = mapped_column(ForeignKey("staff_user.id"))
    approved_by_id: Mapped[str | None] = mapped_column(ForeignKey("staff_user.id"))
    approved_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    steps: Mapped[list[RouteStep]] = relationship(back_populates="route", cascade="all, delete-orphan", order_by="RouteStep.sort")
    recorded_by: Mapped[StaffUser | None] = relationship(foreign_keys=[recorded_by_id])
    approved_by: Mapped[StaffUser | None] = relationship(foreign_keys=[approved_by_id])


class RouteStep(Base):
    __tablename__ = "route_step"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    route_id: Mapped[str] = mapped_column(ForeignKey("route.id", ondelete="CASCADE"), index=True)
    sort: Mapped[int] = mapped_column(Integer, default=0)
    text: Mapped[str] = mapped_column(Text)  # transcript or typed instruction; also the text alternative
    audio_key: Mapped[str | None] = mapped_column(String(300))
    audio_mime: Mapped[str | None] = mapped_column(String(60))

    route: Mapped[Route] = relationship(back_populates="steps")


class PushSubscription(Base):
    __tablename__ = "push_subscription"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"), index=True)
    endpoint: Mapped[str] = mapped_column(String(600), unique=True)
    p256dh: Mapped[str] = mapped_column(String(200))
    auth: Mapped[str] = mapped_column(String(100))
    failures: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Review(Base):
    __tablename__ = "review"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    notice_id: Mapped[str] = mapped_column(ForeignKey("notice.id"), index=True)
    reviewer_id: Mapped[str] = mapped_column(ForeignKey("staff_user.id"))
    decision: Mapped[str] = mapped_column(String(16))  # approved | rejected
    minutes: Mapped[float] = mapped_column(Float, default=0)
    fields_changed: Mapped[list | None] = mapped_column(JSON)
    note: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    reviewer: Mapped[StaffUser] = relationship()


class InboundMessage(Base):
    __tablename__ = "inbound_message"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    wa_message_id: Mapped[str] = mapped_column(String(120), unique=True)  # dedupes Meta retries
    phone_hash: Mapped[str] = mapped_column(String(64), index=True)
    student_id: Mapped[str | None] = mapped_column(ForeignKey("student.id"))
    msg_type: Mapped[str] = mapped_column(String(20))
    text: Mapped[str | None] = mapped_column(Text)
    media_id: Mapped[str | None] = mapped_column(String(120))
    media_mime: Mapped[str | None] = mapped_column(String(80))
    status: Mapped[str] = mapped_column(String(16), default="received")
    received_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ForwardCheck(Base):
    __tablename__ = "forward_check"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    channel: Mapped[str] = mapped_column(String(16))  # whatsapp | web
    student_id: Mapped[str | None] = mapped_column(ForeignKey("student.id"))
    college_id: Mapped[str | None] = mapped_column(ForeignKey("college.id"))
    source_sha256: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[str] = mapped_column(String(16))  # official | superseded | unknown
    similarity: Mapped[float | None] = mapped_column(Float)
    matched_notice_id: Mapped[str | None] = mapped_column(ForeignKey("notice.id"))
    latest_notice_id: Mapped[str | None] = mapped_column(ForeignKey("notice.id"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Job(Base):
    __tablename__ = "job"
    __table_args__ = (Index("ix_job_claim", "status", "run_after"),)
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(40))
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    status: Mapped[str] = mapped_column(String(16), default=JobStatus.queued)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_attempts: Mapped[int] = mapped_column(Integer, default=5)
    run_after: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    locked_until: Mapped[datetime | None] = mapped_column(UTCDateTime)
    locked_by: Mapped[str | None] = mapped_column(String(64))
    last_error: Mapped[str | None] = mapped_column(Text)
    dedupe_key: Mapped[str | None] = mapped_column(String(200), unique=True)
    notice_id: Mapped[str | None] = mapped_column(String(32), index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, onupdate=utcnow)


class ProviderUsage(Base):
    """Every external call, for the budget guard and an honest cost slide."""

    __tablename__ = "provider_usage"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    provider: Mapped[str] = mapped_column(String(30), index=True)
    operation: Mapped[str] = mapped_column(String(30))
    units: Mapped[float] = mapped_column(Float, default=0)
    cost_paise: Mapped[int] = mapped_column(Integer, default=0)
    success: Mapped[bool] = mapped_column(Boolean, default=True)
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OcrCache(Base):
    __tablename__ = "ocr_cache"
    sha256: Mapped[str] = mapped_column(String(64), primary_key=True)
    engine: Mapped[str] = mapped_column(String(40))
    text: Mapped[str] = mapped_column(Text)
    pages: Mapped[int] = mapped_column(Integer, default=1)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AudioCache(Base):
    __tablename__ = "audio_cache"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)  # sha256(text|lang|voice)
    audio_key: Mapped[str] = mapped_column(String(300))
    voice_note_key: Mapped[str | None] = mapped_column(String(300))
    engine: Mapped[str] = mapped_column(String(40))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AuditEvent(Base):
    """Append-only log. Powers the notice timeline and the accessibility log export."""

    __tablename__ = "audit_event"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    college_id: Mapped[str | None] = mapped_column(String(32), index=True)
    notice_id: Mapped[str | None] = mapped_column(String(32), index=True)
    actor: Mapped[str] = mapped_column(String(80))  # "system", "staff:<id>", "student:<id>"
    event: Mapped[str] = mapped_column(String(60))
    detail: Mapped[dict | None] = mapped_column(JSON)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


class OutboundMessage(Base):
    """Maps WhatsApp message ids to deliveries so status webhooks (delivered/read) update the right row."""

    __tablename__ = "outbound_message"
    wa_message_id: Mapped[str] = mapped_column(String(120), primary_key=True)
    delivery_id: Mapped[str | None] = mapped_column(ForeignKey("delivery.id"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="sent")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class LinkCode(Base):
    """Links a website session to a WhatsApp number. Two secrets, both stored only as hashes:
    the code in the QR / pre-filled message (proves the sender saw the QR) and a short confirm code the bot
    sends back to WhatsApp, which the person types on the website (proves the website session and the phone
    are the same person). A photographed QR alone therefore cannot link anyone's number."""

    __tablename__ = "link_code"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"), index=True)  # the website session
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    wa_student_id: Mapped[str | None] = mapped_column(String(32))  # set when the code arrives on WhatsApp
    confirm_hash: Mapped[str | None] = mapped_column(String(64))
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    used_at: Mapped[datetime | None] = mapped_column(UTCDateTime)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class RecentRead(Base):
    """The last notice a student read on WhatsApp, sealed (encrypted) for one hour so they can ask questions about it.
    One row per student; replaced by the next read; erased by DELETE or when it expires."""

    __tablename__ = "recent_read"
    id: Mapped[str] = mapped_column(String(32), primary_key=True, default=new_id)
    student_id: Mapped[str] = mapped_column(ForeignKey("student.id", ondelete="CASCADE"), unique=True)
    sealed: Mapped[str] = mapped_column(Text)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
