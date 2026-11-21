"""WhatsApp Cloud API webhook.

Rules that keep this reliable:
- Verify Meta's signature on the raw body before trusting anything.
- Persist first, process later: we store the message and enqueue a job, then
  return 200 within milliseconds so Meta never retries because we were slow.
- Meta retries deliver the same message id; a UNIQUE constraint drops duplicates.
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse, PlainTextResponse
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..config import get_settings
from ..db import sessionmaker
from ..events import bus
from ..models import Delivery, DeliveryStatus, InboundMessage, OutboundMessage, Student
from ..pipeline.jobs import enqueue
from ..security import encrypt_phone, normalize_phone, phone_hash, verify_meta_signature

log = logging.getLogger("suchana.webhook")
router = APIRouter(prefix="/webhooks/whatsapp", tags=["whatsapp"])

STATUS_RANK = {"sent": 1, "delivered": 2, "read": 3}


@router.get("")
async def verify(mode: str = Query(None, alias="hub.mode"), token: str = Query(None, alias="hub.verify_token"), challenge: str = Query(None, alias="hub.challenge")):
    if mode == "subscribe" and token == get_settings().whatsapp_verify_token:
        return PlainTextResponse(challenge or "")
    return PlainTextResponse("verification failed", status_code=403)


@router.post("")
async def receive(request: Request):
    raw = await request.body()
    if not verify_meta_signature(raw, request.headers.get("x-hub-signature-256")):
        log.warning("rejected webhook with bad signature")
        return JSONResponse({"error": "bad signature"}, status_code=401)
    try:
        payload = json.loads(raw)
    except ValueError:
        return JSONResponse({"error": "bad json"}, status_code=400)

    handled = 0
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            value = change.get("value", {})
            for msg in value.get("messages", []) or []:
                handled += await _store_message(msg)
            for st in value.get("statuses", []) or []:
                await _apply_status(st)
    return {"ok": True, "messages": handled}


async def _store_message(msg: dict) -> int:
    try:
        phone = normalize_phone(msg.get("from", ""))
    except ValueError:
        return 0
    mtype = msg.get("type", "unknown")
    text = None
    media_id = media_mime = None
    if mtype == "text":
        text = (msg.get("text") or {}).get("body", "")[:4000]
    elif mtype in ("image", "document", "audio"):
        media = msg.get(mtype) or {}
        media_id, media_mime = media.get("id"), media.get("mime_type")
        text = media.get("caption")
    elif mtype == "interactive":
        inter = msg.get("interactive") or {}
        text = ((inter.get("button_reply") or inter.get("list_reply") or {}).get("id")) or None
        mtype = "text"
    elif mtype == "button":
        text = (msg.get("button") or {}).get("text")
        mtype = "text"
    try:
        received = datetime.fromtimestamp(int(msg.get("timestamp")), tz=timezone.utc)
    except (TypeError, ValueError):
        received = datetime.now(timezone.utc)

    ph = phone_hash(phone)
    async with sessionmaker()() as s:
        student = await s.scalar(select(Student).where(Student.phone_hash == ph))
        if student is None:
            student = Student(phone_hash=ph, phone_enc=encrypt_phone(phone))
            s.add(student)
            await s.flush()
        inbound = InboundMessage(
            wa_message_id=msg.get("id") or f"noid-{received.timestamp()}",
            phone_hash=ph,
            student_id=student.id,
            msg_type=mtype,
            text=text,
            media_id=media_id,
            media_mime=media_mime,
            received_at=received,
        )
        s.add(inbound)
        try:
            await s.flush()
        except IntegrityError:
            await s.rollback()
            return 0  # duplicate delivery from Meta
        # No phone number in the job payload: the handler decrypts it from the student row.
        await enqueue(
            s,
            "handle_inbound",
            {"inbound_id": inbound.id, "wa_message_id": inbound.wa_message_id},
            dedupe_key=f"inbound:{inbound.wa_message_id}",
            max_attempts=4,
        )
        await s.commit()
    return 1


async def _apply_status(st: dict) -> None:
    mid, status = st.get("id"), st.get("status")
    if not mid or status not in ("sent", "delivered", "read", "failed"):
        return
    async with sessionmaker()() as s:
        om = await s.get(OutboundMessage, mid)
        if om is None or om.delivery_id is None:
            return
        d = await s.get(Delivery, om.delivery_id)
        if d is None:
            return
        om.status = status
        if status == "failed":
            errs = st.get("errors") or [{}]
            d.status = DeliveryStatus.failed
            d.error = f"{errs[0].get('code')}: {errs[0].get('title') or errs[0].get('message')}"
        elif STATUS_RANK.get(status, 0) > STATUS_RANK.get(d.status, 0):
            # Statuses can arrive out of order; never downgrade "read" to "delivered".
            d.status = status
        await s.commit()
        bus.publish("delivery.updated", {"notice_id": d.notice_id, "status": d.status})
