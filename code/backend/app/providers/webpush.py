"""Web Push (RFC 8030 + VAPID RFC 8292 + message encryption RFC 8291), written directly on
`cryptography` because the usual helper library needs a C build step.

This is what makes Buzz Codes possible: the phone's own notification system vibrates
with a pattern chosen per message type, even when the app is closed (Android Chrome).
"""

from __future__ import annotations

import base64
import json
import os
import struct
import time
from urllib.parse import urlparse

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import ec
from cryptography.hazmat.primitives.asymmetric.utils import decode_dss_signature
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

from ..config import get_settings
from .base import ProviderError, ProviderNotConfigured, record_usage


def b64e(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def b64d(data: str) -> bytes:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))


def generate_vapid_keys() -> tuple[str, str]:
    """Return (private_key_b64url, public_key_b64url) for the .env file."""
    key = ec.generate_private_key(ec.SECP256R1())
    raw_private = key.private_numbers().private_value.to_bytes(32, "big")
    public = key.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    return b64e(raw_private), b64e(public)


def _vapid_key() -> ec.EllipticCurvePrivateKey:
    s = get_settings()
    if not (s.vapid_private_key and s.vapid_public_key):
        raise ProviderNotConfigured("webpush", "VAPID_PRIVATE_KEY / VAPID_PUBLIC_KEY")
    return ec.derive_private_key(int.from_bytes(b64d(s.vapid_private_key), "big"), ec.SECP256R1())


def vapid_headers(endpoint: str, *, now: float | None = None) -> dict:
    s = get_settings()
    key = _vapid_key()
    u = urlparse(endpoint)
    claims = {"aud": f"{u.scheme}://{u.netloc}", "exp": int((now or time.time()) + 12 * 3600), "sub": s.vapid_subject}
    signing_input = b64e(json.dumps({"typ": "JWT", "alg": "ES256"}).encode()) + "." + b64e(json.dumps(claims).encode())
    r, sv = decode_dss_signature(key.sign(signing_input.encode(), ec.ECDSA(hashes.SHA256())))
    jwt = signing_input + "." + b64e(r.to_bytes(32, "big") + sv.to_bytes(32, "big"))
    return {"Authorization": f"vapid t={jwt}, k={s.vapid_public_key}"}


def encrypt_payload(payload: bytes, ua_public_b64: str, auth_b64: str, *, salt: bytes | None = None, as_private: ec.EllipticCurvePrivateKey | None = None) -> bytes:
    """RFC 8291 aes128gcm content encoding, single record."""
    ua_public = b64d(ua_public_b64)
    auth_secret = b64d(auth_b64)
    as_private = as_private or ec.generate_private_key(ec.SECP256R1())
    as_public = as_private.public_key().public_bytes(serialization.Encoding.X962, serialization.PublicFormat.UncompressedPoint)
    ecdh = as_private.exchange(ec.ECDH(), ec.EllipticCurvePublicKey.from_encoded_point(ec.SECP256R1(), ua_public))
    salt = salt or os.urandom(16)
    ikm = HKDF(hashes.SHA256(), 32, auth_secret, b"WebPush: info\x00" + ua_public + as_public).derive(ecdh)
    cek = HKDF(hashes.SHA256(), 16, salt, b"Content-Encoding: aes128gcm\x00").derive(ikm)
    nonce = HKDF(hashes.SHA256(), 12, salt, b"Content-Encoding: nonce\x00").derive(ikm)
    ciphertext = AESGCM(cek).encrypt(nonce, payload + b"\x02", None)  # 0x02 = last record delimiter
    return salt + struct.pack(">IB", 4096, len(as_public)) + as_public + ciphertext


async def send_push(endpoint: str, p256dh: str, auth: str, payload: dict, *, ttl: int = 3600, urgency: str = "high") -> None:
    body = encrypt_payload(json.dumps(payload, ensure_ascii=False).encode(), p256dh, auth)
    headers = {**vapid_headers(endpoint), "Content-Encoding": "aes128gcm", "Content-Type": "application/octet-stream", "TTL": str(ttl), "Urgency": urgency}
    try:
        async with httpx.AsyncClient(timeout=15) as http:
            r = await http.post(endpoint, content=body, headers=headers)
    except httpx.HTTPError as exc:
        await record_usage("webpush", "send", success=False, error=str(exc))
        raise ProviderError("webpush", f"network: {exc}", retryable=True) from exc
    if r.status_code in (404, 410):
        raise ProviderError("webpush", "subscription expired", retryable=False, status=r.status_code)
    if r.status_code >= 400:
        await record_usage("webpush", "send", success=False, error=f"HTTP {r.status_code}")
        raise ProviderError("webpush", f"HTTP {r.status_code}: {r.text[:200]}", retryable=r.status_code >= 500 or r.status_code == 429, status=r.status_code)
    await record_usage("webpush", "send", units=1)
