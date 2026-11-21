"""Auth and data protection.

- Staff authenticate with a bearer token (stored as a SHA-256 hash).
- Students never log in: the bot sends them a signed link to their inbox.
- Phone numbers are encrypted at rest (Fernet) and looked up via an HMAC.
- WhatsApp webhooks are verified with Meta's X-Hub-Signature-256 header.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import logging
import secrets
import time

from cryptography.fernet import Fernet, InvalidToken
from fastapi import Depends, Header
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from .config import get_settings
from .db import get_session
from .errors import Forbidden, Unauthorized
from .models import StaffRole, StaffUser

log = logging.getLogger("suchana.security")


# --- Staff tokens ---------------------------------------------------------------


def new_staff_token() -> str:
    return "ss_" + secrets.token_urlsafe(24)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


async def current_staff(
    authorization: str | None = Header(default=None),
    session: AsyncSession = Depends(get_session),
) -> StaffUser:
    if not authorization or not authorization.lower().startswith("bearer "):
        raise Unauthorized("Sign in to continue.", code="missing_token")
    token = authorization.split(" ", 1)[1].strip()
    staff = await session.scalar(select(StaffUser).where(StaffUser.token_hash == hash_token(token)))
    if staff is None or not staff.active:
        raise Unauthorized("This sign-in link is not valid any more.", code="invalid_token")
    return staff


def require_role(*roles: StaffRole):
    async def _dep(staff: StaffUser = Depends(current_staff)) -> StaffUser:
        if staff.role not in roles:
            raise Forbidden("Your role cannot do this.", code="role_forbidden")
        return staff

    return _dep


# --- Signed student links -----------------------------------------------------------


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def sign_student_token(student_id: str, ttl_days: int = 60) -> str:
    payload = _b64(json.dumps({"s": student_id, "e": int(time.time()) + ttl_days * 86400}).encode())
    sig = hmac.new(get_settings().secret_key.encode(), payload.encode(), hashlib.sha256).digest()
    return f"{payload}.{_b64(sig[:16])}"


def verify_student_token(token: str) -> str:
    try:
        payload, sig = token.split(".", 1)
        expected = hmac.new(get_settings().secret_key.encode(), payload.encode(), hashlib.sha256).digest()[:16]
        if not hmac.compare_digest(_unb64(sig), expected):
            raise ValueError("bad signature")
        data = json.loads(_unb64(payload))
        if data["e"] < time.time():
            raise Unauthorized("This link has expired. Send 'inbox' to the bot for a new one.", code="link_expired")
        return data["s"]
    except Unauthorized:
        raise
    except Exception as exc:
        raise Unauthorized("This link is not valid.", code="invalid_link") from exc


# --- Phone numbers ------------------------------------------------------------------------


def normalize_phone(raw: str) -> str:
    """Return digits with country code, e.g. '919876543210'. Assumes India for 10-digit numbers."""
    digits = "".join(ch for ch in raw if ch.isdigit())
    if len(digits) == 10:
        digits = "91" + digits
    elif len(digits) == 11 and digits.startswith("0"):
        digits = "91" + digits[1:]
    if len(digits) < 11 or len(digits) > 15:
        raise ValueError("phone number looks invalid")
    return digits


def phone_hash(phone: str) -> str:
    return hmac.new(get_settings().secret_key.encode(), normalize_phone(phone).encode(), hashlib.sha256).hexdigest()


def _fernet() -> Fernet | None:
    key = get_settings().phone_encryption_key
    return Fernet(key.encode()) if key else None


def encrypt_phone(phone: str) -> str:
    phone = normalize_phone(phone)
    f = _fernet()
    if f is None:
        if get_settings().is_prod:
            raise RuntimeError("PHONE_ENCRYPTION_KEY is required in prod")
        return "plain:" + phone  # dev only, clearly marked
    return "fernet:" + f.encrypt(phone.encode()).decode()


def decrypt_phone(stored: str) -> str:
    if stored.startswith("plain:"):
        return stored[6:]
    if stored.startswith("fernet:"):
        f = _fernet()
        if f is None:
            raise RuntimeError("PHONE_ENCRYPTION_KEY missing; cannot read stored phone numbers")
        try:
            return f.decrypt(stored[7:].encode()).decode()
        except InvalidToken as exc:
            raise RuntimeError("PHONE_ENCRYPTION_KEY changed; stored phone numbers cannot be read") from exc
    raise RuntimeError("unknown phone encoding")


def mask_phone(phone: str) -> str:
    return phone[:-6] + "xx" + phone[-4:] if len(phone) > 6 else "xxxx"


# --- WhatsApp webhook signature ------------------------------------------------------------


def verify_meta_signature(raw_body: bytes, header: str | None) -> bool:
    secret = get_settings().whatsapp_app_secret
    if not secret:
        if get_settings().is_prod:
            return False
        log.warning("WHATSAPP_APP_SECRET not set: accepting unsigned webhook (dev only)")
        return True
    if not header or not header.startswith("sha256="):
        return False
    expected = hmac.new(secret.encode(), raw_body, hashlib.sha256).hexdigest()
    return hmac.compare_digest(header[7:], expected)
