"""Speech to text for voice-note questions: Sarvam Saaras -> Gemini audio."""

from __future__ import annotations

import logging
import math

from ..config import get_settings
from .base import (
    ProviderError,
    ProviderNotConfigured,
    Timer,
    record_usage,
    refund_budget,
    reserve_budget,
    with_retries,
)
from .llm import gen_config, make_client

log = logging.getLogger("suchana.stt")


def _estimate_minutes(audio: bytes) -> int:
    # OGG/Opus voice notes are ~2-4 KB per second; round up generously.
    return max(1, math.ceil(len(audio) / (2500 * 60)))


async def sarvam_stt(audio: bytes, mime: str) -> str:
    s = get_settings()
    if not s.sarvam_api_key:
        raise ProviderNotConfigured("sarvam", "SARVAM_API_KEY")
    minutes = _estimate_minutes(audio)
    if minutes > 2:
        raise ProviderError("sarvam", "voice note longer than 2 minutes", retryable=False)
    cost = minutes * s.sarvam_stt_paise_per_min
    await reserve_budget("sarvam", cost, s.sarvam_budget_paise)
    from sarvamai import AsyncSarvamAI

    client = AsyncSarvamAI(api_subscription_key=s.sarvam_api_key)
    ext = next((e for k, e in (("ogg", "ogg"), ("webm", "webm"), ("mpeg", "mp3"), ("wav", "wav")) if k in mime), "m4a")

    async def _run() -> str:
        kwargs = {"file": (f"voice.{ext}", audio, mime.split(";")[0])}
        if s.sarvam_stt_model:
            kwargs["model"] = s.sarvam_stt_model
        resp = await client.speech_to_text.transcribe(**kwargs)
        return (resp.transcript or "").strip()

    with Timer() as t:
        try:
            text = await with_retries("sarvam", _run, attempts=2, timeout=60)
        except Exception as exc:
            await refund_budget("sarvam", cost)
            await record_usage("sarvam", "stt", success=False, error=str(exc))
            raise
    await record_usage("sarvam", "stt", units=minutes, latency_ms=t.ms)
    return text


async def gemini_stt(audio: bytes, mime: str) -> str:
    s = get_settings()
    if not s.gemini_api_key:
        raise ProviderNotConfigured("gemini", "GEMINI_API_KEY")
    from google.genai import types

    client = make_client(s.gemini_api_key)

    async def _run() -> str:
        resp = await client.aio.models.generate_content(
            model=s.gemini_model,
            contents=[
                types.Part.from_bytes(data=audio, mime_type=mime.split(";")[0]),
                "Transcribe this voice message exactly, in its original language and script. Output only the words.",
            ],
            config=gen_config(temperature=0),
        )
        return (resp.text or "").strip()

    with Timer() as t:
        try:
            text = await with_retries("gemini", _run, attempts=2, timeout=60)
        except Exception as exc:
            await record_usage("gemini", "stt", success=False, error=str(exc))
            raise
    await record_usage("gemini", "stt", latency_ms=t.ms)
    return text


async def transcribe(audio: bytes, mime: str) -> str:
    errors = []
    for fn in (sarvam_stt, gemini_stt):
        try:
            text = await fn(audio, mime)
            if text:
                return text
        except ProviderError as exc:
            errors.append(str(exc))
    raise ProviderError("stt", " | ".join(errors) or "empty transcript", retryable=False)
