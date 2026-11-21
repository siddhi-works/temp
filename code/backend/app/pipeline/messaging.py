"""One way to reach a student: WhatsApp (voice + text) and a phone vibration via Web Push.

Channel failures never raise: a closed WhatsApp window or a dead push subscription must not
fail the job that wanted to tell the student something. The result says what worked.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..models import PushSubscription, Student
from ..providers import webpush, whatsapp
from ..providers.base import ProviderError
from ..providers.tts import synthesize
from ..security import decrypt_phone
from ..storage import get_storage
from .buzz import pattern_for

log = logging.getLogger("suchana.messaging")


def in_service_window(student: Student) -> bool:
    from datetime import timedelta

    from ..db import utcnow

    return bool(student.last_inbound_at and utcnow() - student.last_inbound_at < timedelta(hours=23, minutes=30))


async def push_to_student(s: AsyncSession, student: Student, *, kind: str, title: str, body: str, url: str | None = None) -> int:
    """Send a vibrating notification to every device the student registered. Returns how many accepted it."""
    cfg = get_settings()
    if not (cfg.vapid_private_key and cfg.vapid_public_key):
        return 0
    subs = (await s.scalars(select(PushSubscription).where(PushSubscription.student_id == student.id))).all()
    ok = 0
    for sub in subs:
        payload = {"title": title, "body": body, "url": url, "type": kind, "vibrate": pattern_for(kind, student.buzz_prefs), "tag": kind}
        try:
            await webpush.send_push(sub.endpoint, sub.p256dh, sub.auth, payload)
            sub.failures = 0
            ok += 1
        except ProviderError as exc:
            if exc.status in (404, 410):
                await s.delete(sub)  # the browser unsubscribed; stop trying
            else:
                sub.failures += 1
                if sub.failures >= 5:
                    await s.delete(sub)
                log.info("push failed: %s", exc)
    await s.commit()
    return ok


async def notify_student(
    s: AsyncSession,
    student: Student,
    *,
    kind: str,
    title: str,
    text: str,
    speak: str | None = None,
    lang: str | None = None,
    url: str | None = None,
) -> dict:
    """Voice note (if `speak`) then text on WhatsApp, plus a push. Returns {"whatsapp": bool, "voice": bool, "push": int}."""
    cfg = get_settings()
    result = {"whatsapp": False, "voice": False, "push": 0}
    if student.opted_out_at:
        return result
    if student.phone_enc == "none":  # web-only student: no phone number, push notifications only
        result["push"] = await push_to_student(s, student, kind=kind, title=title, body=text[:240], url=url)
        return result
    phone = decrypt_phone(student.phone_enc)
    try:
        if not in_service_window(student):
            raise whatsapp.WindowClosed()
        if speak:
            try:
                audio = await synthesize(speak, lang or student.preferred_lang or "en")
                key = audio.voice_note_key or audio.audio_key
                data = await get_storage().get(key)
                media_id = await whatsapp.upload_media(data, "audio/ogg" if key.endswith(".ogg") else "audio/mpeg", key.rsplit("/", 1)[-1])
                await whatsapp.send_audio(phone, media_id)
                result["voice"] = True
            except whatsapp.WindowClosed:
                raise
            except ProviderError as exc:
                log.info("voice skipped: %s", exc)
        await whatsapp.send_text(phone, text)
        result["whatsapp"] = True
    except whatsapp.WindowClosed:
        if cfg.whatsapp_notice_template:
            try:
                await whatsapp.send_template(phone, cfg.whatsapp_notice_template, cfg.whatsapp_template_lang, [title, text[:300], url or cfg.frontend_base_url])
                result["whatsapp"] = True
            except ProviderError as exc:
                log.info("template failed: %s", exc)
    except ProviderError as exc:
        log.warning("whatsapp send failed: %s", exc)
    result["push"] = await push_to_student(s, student, kind=kind, title=title, body=text[:240], url=url)
    return result
