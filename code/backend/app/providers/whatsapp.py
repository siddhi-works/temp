"""Meta WhatsApp Cloud API client (official API only; no unofficial libraries)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from ..config import get_settings
from .base import (
    ProviderError,
    ProviderNotConfigured,
    Timer,
    record_usage,
    with_retries,
)

log = logging.getLogger("suchana.whatsapp")

# Meta error codes we handle specifically.
RE_ENGAGEMENT = 131047  # more than 24h since the user last messaged: template required
RATE_LIMITS = {4, 80007, 130429, 131048, 131056}
NOT_ON_WHATSAPP = {131026}


class WindowClosed(ProviderError):
    def __init__(self) -> None:
        super().__init__("whatsapp", "24-hour window closed; a template message is required", retryable=False)


@dataclass
class SentMessage:
    message_id: str


def _base() -> str:
    s = get_settings()
    return f"https://graph.facebook.com/{s.whatsapp_graph_version}"


def _headers() -> dict:
    s = get_settings()
    if not s.whatsapp_token or not s.whatsapp_phone_number_id:
        raise ProviderNotConfigured("whatsapp", "WHATSAPP_TOKEN / WHATSAPP_PHONE_NUMBER_ID")
    return {"Authorization": f"Bearer {s.whatsapp_token}"}


def _raise_for_meta(resp: httpx.Response) -> None:
    if resp.status_code < 400:
        return
    try:
        err = resp.json().get("error", {})
    except ValueError:
        err = {}
    code = err.get("code")
    msg = err.get("message") or resp.text[:200]
    if code == RE_ENGAGEMENT:
        raise WindowClosed()
    if code in RATE_LIMITS or resp.status_code == 429:
        raise ProviderError("whatsapp", f"rate limited ({code}): {msg}", retryable=True, status=429)
    if code in NOT_ON_WHATSAPP:
        raise ProviderError("whatsapp", f"recipient not reachable ({code}): {msg}", retryable=False, status=400)
    raise ProviderError(
        "whatsapp", f"HTTP {resp.status_code} code={code}: {msg}", retryable=resp.status_code >= 500, status=resp.status_code
    )


async def _post_message(body: dict, op: str) -> SentMessage:
    s = get_settings()
    headers = _headers()

    async def _run() -> SentMessage:
        async with httpx.AsyncClient(timeout=20) as http:
            r = await http.post(f"{_base()}/{s.whatsapp_phone_number_id}/messages", headers=headers, json=body)
            _raise_for_meta(r)
            return SentMessage(r.json()["messages"][0]["id"])

    with Timer() as t:
        try:
            out = await with_retries("whatsapp", _run, attempts=3, timeout=30)
        except Exception as exc:
            await record_usage("whatsapp", op, success=False, error=str(exc))
            raise
    await record_usage("whatsapp", op, units=1, latency_ms=t.ms)
    return out


async def send_text(to: str, text: str, *, preview_url: bool = True) -> SentMessage:
    return await _post_message(
        {
            "messaging_product": "whatsapp",
            "recipient_type": "individual",
            "to": to,
            "type": "text",
            "text": {"body": text[:4096], "preview_url": preview_url},
        },
        "send_text",
    )


async def upload_media(data: bytes, mime: str, filename: str) -> str:
    s = get_settings()
    headers = _headers()

    async def _run() -> str:
        async with httpx.AsyncClient(timeout=60) as http:
            r = await http.post(
                f"{_base()}/{s.whatsapp_phone_number_id}/media",
                headers=headers,
                data={"messaging_product": "whatsapp", "type": mime},
                files={"file": (filename, data, mime)},
            )
            _raise_for_meta(r)
            return r.json()["id"]

    return await with_retries("whatsapp", _run, attempts=3, timeout=70)


async def send_audio(to: str, media_id: str) -> SentMessage:
    return await _post_message(
        {"messaging_product": "whatsapp", "recipient_type": "individual", "to": to, "type": "audio", "audio": {"id": media_id}},
        "send_audio",
    )


async def send_template(to: str, name: str, lang: str, params: list[str]) -> SentMessage:
    return await _post_message(
        {
            "messaging_product": "whatsapp",
            "to": to,
            "type": "template",
            "template": {
                "name": name,
                "language": {"code": lang},
                "components": [{"type": "body", "parameters": [{"type": "text", "text": p[:1000]} for p in params]}],
            },
        },
        "send_template",
    )


async def download_media(media_id: str) -> tuple[bytes, str]:
    headers = _headers()

    async def _run() -> tuple[bytes, str]:
        async with httpx.AsyncClient(timeout=60, follow_redirects=True) as http:
            meta = await http.get(f"{_base()}/{media_id}", headers=headers)
            _raise_for_meta(meta)
            info = meta.json()
            if int(info.get("file_size") or 0) > get_settings().max_upload_mb * 1024 * 1024:
                raise ProviderError("whatsapp", "file too large", retryable=False)
            blob = await http.get(info["url"], headers=headers)
            _raise_for_meta(blob)
            return blob.content, info.get("mime_type", "application/octet-stream")

    return await with_retries("whatsapp", _run, attempts=3, timeout=90)


async def mark_read(message_id: str) -> None:
    s = get_settings()
    try:
        async with httpx.AsyncClient(timeout=10) as http:
            await http.post(
                f"{_base()}/{s.whatsapp_phone_number_id}/messages",
                headers=_headers(),
                json={"messaging_product": "whatsapp", "status": "read", "message_id": message_id},
            )
    except Exception:
        log.debug("mark_read failed", exc_info=True)
