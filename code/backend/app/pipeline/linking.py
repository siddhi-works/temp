"""Link a website session to a WhatsApp number, unlink it, and erase a student. Used by the website API and the bot.

How a link is made, and why it is safe:

1. The website (signed in with its own token) asks for a link code. We store only its hash, valid for 10 minutes.
2. The code goes into a WhatsApp click-to-chat link and QR ("Hi Samyukta, my link code is K7M2QX4P"). The QR holds no
   personal data: just our public number and a random code.
3. The student presses Send. The webhook (signature-verified) tells us which WhatsApp number sent it. The bot replies
   on WhatsApp with a 4-digit confirm code. A phone number alone proves nothing, so the link is NOT made yet.
4. The student types the 4-digit code on the website, which is signed in. Only someone who holds both the website
   session and the phone can do that. Five wrong tries kill the code. Then the two records are merged.

Someone who photographs the QR and sends the code first only gets a confirm code on their own phone. They cannot
enter it on the victim's website session, and the website shows the last 4 digits of the number that claimed the code,
so the real student sees a number that is not theirs.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import new_id, utcnow
from ..errors import AppError
from ..models import (
    Delivery,
    ForwardCheck,
    InboundMessage,
    LinkCode,
    Notice,
    OutboundMessage,
    PersonalDeadline,
    PushSubscription,
    RecentRead,
    Reminder,
    Snap,
    Student,
    StudentSlot,
)
from ..security import decrypt_phone, mask_phone
from ..storage import get_storage

LINK_TTL = timedelta(minutes=10)
MAX_CONFIRM_TRIES = 5
ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0, O, 1, I or L: nothing to mix up when read aloud
CODE_LEN = 8

# Tables that hold a student's own rows. Deleting or moving a student means touching exactly these.
OWNED = (InboundMessage, ForwardCheck, PersonalDeadline, PushSubscription, Snap, StudentSlot, Reminder)


def _mac(kind: str, value: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), f"{kind}:{value.strip().upper()}".encode(), hashlib.sha256).hexdigest()


def is_web_only(student: Student) -> bool:
    """A website student has a random placeholder instead of a phone number."""
    return student.phone_hash.startswith("web-")


async def has_completed_link(s: AsyncSession, student: Student) -> bool:
    """True if this student record is a website account that took over a WhatsApp number. A record that merely
    started on WhatsApp has no completed link, and may still connect to a website session."""
    return bool(await s.scalar(select(LinkCode.id).where(LinkCode.student_id == student.id, LinkCode.used_at.is_not(None)).limit(1)))


def whatsapp_link(code: str | None = None) -> str | None:
    """The click-to-chat address: opens WhatsApp on the official number with the message already typed."""
    from urllib.parse import quote

    number = "".join(ch for ch in get_settings().whatsapp_business_number if ch.isdigit())
    if not number:
        return None
    text = f"Hi Samyukta, my link code is {code}" if code else "Hi Samyukta"
    return f"https://wa.me/{number}?text={quote(text)}"


def qr_svg(data: str) -> str:
    """A QR code as an SVG string (no image library needed, scales without blurring)."""
    import qrcode
    import qrcode.image.svg

    img = qrcode.make(data, image_factory=qrcode.image.svg.SvgPathImage, box_size=10, border=2)
    from io import BytesIO

    buf = BytesIO()
    img.save(buf)
    return buf.getvalue().decode()


async def start_link(s: AsyncSession, student: Student) -> tuple[str, LinkCode]:
    if not is_web_only(student):
        raise AppError("This account is already connected to WhatsApp.", code="already_linked", status_code=409)
    await s.execute(delete(LinkCode).where(LinkCode.student_id == student.id, LinkCode.used_at.is_(None)))
    code = "".join(secrets.choice(ALPHABET) for _ in range(CODE_LEN))
    row = LinkCode(student_id=student.id, code_hash=_mac("link", code), expires_at=utcnow() + LINK_TTL)
    s.add(row)
    await s.commit()
    return code, row


async def link_status(s: AsyncSession, student: Student) -> dict:
    if not is_web_only(student):
        return {"linked": True, "number": mask_phone(decrypt_phone(student.phone_enc)), "waiting": False}
    row = await s.scalar(
        select(LinkCode).where(LinkCode.student_id == student.id, LinkCode.used_at.is_(None), LinkCode.expires_at > utcnow()).order_by(LinkCode.created_at.desc()).limit(1)
    )
    claimed = None
    if row is not None and row.wa_student_id:
        wa = await s.get(Student, row.wa_student_id)
        claimed = decrypt_phone(wa.phone_enc)[-4:] if wa else None
    return {"linked": False, "number": None, "waiting": row is not None, "claimed_by_last4": claimed}


async def claim_link(s: AsyncSession, wa_student: Student, code: str) -> str:
    """WhatsApp side. The sender sent a link code. Returns the reply to send (it contains the confirm code on success)."""
    row = await s.scalar(select(LinkCode).where(LinkCode.code_hash == _mac("link", code)))
    if row is None or row.used_at is not None or row.expires_at <= utcnow():
        return "That link code is not valid, or it has expired. Open Samyukta on the website and tap Connect WhatsApp again."
    web = await s.get(Student, row.student_id)
    if web is None or not is_web_only(web):
        return "That link code is not valid, or it has expired. Open Samyukta on the website and tap Connect WhatsApp again."
    if wa_student.id == web.id:
        return "This WhatsApp number is already connected to your Samyukta account."
    if await has_completed_link(s, wa_student):
        return "This WhatsApp number is already connected to a Samyukta account. Send DISCONNECT here first if you want to connect it somewhere else."
    if row.wa_student_id and row.wa_student_id != wa_student.id:
        return "That link code has already been used from another number. Please start again on the website."
    confirm = f"{secrets.randbelow(10000):04d}"
    row.wa_student_id = wa_student.id
    row.confirm_hash = _mac(f"confirm:{row.id}", confirm)
    row.attempts = 0
    row.expires_at = utcnow() + LINK_TTL
    await s.commit()
    return (
        f"Almost done. To finish connecting, type this 4-digit code on the Samyukta website: {' '.join(confirm)}. "
        "It works for 10 minutes. If you did not ask to connect, ignore this message and nothing will be connected."
    )


async def confirm_link(s: AsyncSession, web: Student, code: str) -> dict:
    """Website side: the signed-in student types the confirm code that WhatsApp sent."""
    row = await s.scalar(
        select(LinkCode)
        .where(LinkCode.student_id == web.id, LinkCode.used_at.is_(None), LinkCode.expires_at > utcnow(), LinkCode.wa_student_id.is_not(None))
        .order_by(LinkCode.created_at.desc())
        .limit(1)
    )
    if row is None:
        raise AppError("Nothing is waiting to be confirmed. Send the message on WhatsApp first, or start again.", code="nothing_to_confirm", status_code=404)
    if row.attempts >= MAX_CONFIRM_TRIES:
        raise AppError("Too many wrong codes. Please start again.", code="too_many_tries", status_code=429)
    row.attempts += 1
    clean = "".join(ch for ch in code if ch.isdigit())
    if not hmac.compare_digest(_mac(f"confirm:{row.id}", clean), row.confirm_hash or ""):
        await s.commit()
        raise AppError("That code is not right. Check the message on WhatsApp and try again.", code="bad_confirm_code", status_code=400)
    wa = await s.get(Student, row.wa_student_id)
    if wa is None or not is_web_only(web):
        raise AppError("This link is no longer valid. Please start again.", code="link_invalid", status_code=409)
    last4 = decrypt_phone(wa.phone_enc)[-4:]
    await merge_into(s, keep=web, drop=wa)
    row.used_at = utcnow()
    await s.commit()
    return {"linked": True, "number": f"xxxxxx{last4}"}


async def merge_into(s: AsyncSession, *, keep: Student, drop: Student) -> None:
    """Move everything the WhatsApp record has onto the website record, then remove the empty WhatsApp record.
    The website record stays, so the student's website token keeps working."""
    keep_id, drop_id = keep.id, drop.id
    carry = dict(
        phone_hash=drop.phone_hash,
        phone_enc=drop.phone_enc,
        last_inbound_at=drop.last_inbound_at,
        opted_out_at=drop.opted_out_at,
        consent_at=keep.consent_at or drop.consent_at,
        college_id=keep.college_id or drop.college_id,
        audience_tags=keep.audience_tags or drop.audience_tags,
    )
    await s.execute(delete(RecentRead).where(RecentRead.student_id == keep_id))
    for model in (*OWNED, RecentRead):
        await s.execute(update(model).where(model.student_id == drop_id).values(student_id=keep_id))
    await s.execute(update(Notice).where(Notice.forwarded_by_student_id == drop_id).values(forwarded_by_student_id=keep_id))
    await s.execute(update(Delivery).where(Delivery.student_id == drop_id).values(student_id=keep_id))
    await s.execute(delete(LinkCode).where(LinkCode.student_id == drop_id))
    s.expunge(drop)
    await s.execute(delete(Student).where(Student.id == drop_id))  # frees the unique phone_hash
    await s.flush()
    for k, v in carry.items():
        setattr(keep, k, v)
    await s.flush()


async def disconnect(s: AsyncSession, student: Student) -> bool:
    """Forget the phone number. Saved deadlines and everything else stay with the website account.
    Returns False if there was nothing to disconnect."""
    if is_web_only(student):
        return False
    await s.execute(delete(RecentRead).where(RecentRead.student_id == student.id))
    await s.execute(delete(LinkCode).where(LinkCode.student_id == student.id))  # the link is over, so is the record of it
    await s.execute(delete(InboundMessage).where(InboundMessage.student_id == student.id))  # what they typed to the bot
    student.phone_hash = "web-" + secrets.token_hex(16)
    student.phone_enc = "none"
    student.last_inbound_at = None
    student.opted_out_at = None
    student.catch_until = None
    await s.commit()
    return True


async def erase_student(s: AsyncSession, student: Student) -> None:
    """Delete the student and everything that belongs to them: deadlines, reminders, board photos (and their files),
    messages, push subscriptions, sealed notice. College notices a student once forwarded are kept, without the link to them."""
    sid = student.id
    for key in (await s.scalars(select(Snap.source_key).where(Snap.student_id == sid, Snap.source_key.is_not(None)))).all():
        try:
            await get_storage().delete(key)
        except Exception:  # noqa: BLE001  (a missing file must not stop an erase)
            pass
    delivery_ids = (await s.scalars(select(Delivery.id).where(Delivery.student_id == sid))).all()
    if delivery_ids:
        await s.execute(delete(OutboundMessage).where(OutboundMessage.delivery_id.in_(delivery_ids)))
    await s.execute(delete(Delivery).where(Delivery.student_id == sid))
    for model in (*OWNED, RecentRead, LinkCode):
        await s.execute(delete(model).where(model.student_id == sid))
    await s.execute(update(Notice).where(Notice.forwarded_by_student_id == sid).values(forwarded_by_student_id=None))
    s.expunge(student)
    await s.execute(delete(Student).where(Student.id == sid))
    await s.commit()


# --------------------------------------------------------------------------- the one-hour sealed notice

RECENT_TTL = timedelta(hours=1)


async def remember_read(s: AsyncSession, student: Student, payload: dict) -> None:
    """Keep the last notice, encrypted, for an hour so the student can ask about it. Replaces the previous one."""
    import json

    from ..security import seal

    await s.execute(delete(RecentRead).where(RecentRead.student_id == student.id))
    s.add(RecentRead(id=new_id(), student_id=student.id, sealed=seal(json.dumps(payload, ensure_ascii=False)), expires_at=utcnow() + RECENT_TTL))
    await s.commit()


async def recall_read(s: AsyncSession, student: Student) -> dict | None:
    import json

    from ..security import unseal

    row = await s.scalar(select(RecentRead).where(RecentRead.student_id == student.id))
    if row is None:
        return None
    text = unseal(row.sealed, int(RECENT_TTL.total_seconds()))
    if row.expires_at <= utcnow() or text is None:
        await s.delete(row)
        await s.commit()
        return None
    return json.loads(text)


async def forget_read(s: AsyncSession, student: Student) -> None:
    await s.execute(delete(RecentRead).where(RecentRead.student_id == student.id))
    await s.commit()
