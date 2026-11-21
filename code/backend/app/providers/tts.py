"""Text to speech chain: Sarvam Bulbul -> Google Cloud TTS -> (text only).

Audio is cached by sha256(text|lang|voice) so rehearsals and re-sends are free.
"""

from __future__ import annotations

import base64
import logging
import re
from dataclasses import dataclass

import httpx
from sqlalchemy import select

from ..config import get_settings
from ..db import sessionmaker
from ..models import AudioCache
from ..storage import get_storage, sha256_bytes
from .base import (
    ProviderError,
    ProviderNotConfigured,
    Timer,
    breaker,
    record_usage,
    refund_budget,
    reserve_budget,
    with_retries,
)
from .media import concat_mp3, mp3_to_voice_note, wav_to_mp3

log = logging.getLogger("suchana.tts")

LANG_CODES = {"en": "en-IN", "mr": "mr-IN", "hi": "hi-IN"}
SARVAM_MAX_CHARS = 2400  # API limit is 2500 for bulbul:v3


@dataclass
class TtsResult:
    audio_key: str
    voice_note_key: str | None
    engine: str
    cached: bool = False


def chunk_text(text: str, limit: int) -> list[str]:
    """Split on sentence ends (English '.', Devanagari danda '।') without breaking words."""
    text = re.sub(r"\s+", " ", text).strip()
    if len(text) <= limit:
        return [text]
    sentences = re.split(r"(?<=[.!?।])\s+", text)
    chunks, cur = [], ""
    for sent in sentences:
        while len(sent) > limit:  # pathological sentence: hard split at a space
            idx = sent.rfind(" ", 0, limit)
            cut = idx if idx > 0 else limit
            chunks.append(sent[:cut].strip())
            sent = sent[cut:].strip()
        if len(cur) + len(sent) + 1 > limit and cur:
            chunks.append(cur)
            cur = sent
        else:
            cur = f"{cur} {sent}".strip()
    if cur:
        chunks.append(cur)
    return chunks


async def sarvam_tts(text: str, lang: str) -> bytes:
    s = get_settings()
    if not s.sarvam_api_key:
        raise ProviderNotConfigured("sarvam", "SARVAM_API_KEY")
    cost = max(1, round(len(text) * s.sarvam_tts_paise_per_1k_chars / 1000))
    await reserve_budget("sarvam", cost, s.sarvam_budget_paise)

    from sarvamai import AsyncSarvamAI

    client = AsyncSarvamAI(api_subscription_key=s.sarvam_api_key)

    async def _one(chunk: str) -> bytes:
        kwargs = {"text": chunk, "language_code": LANG_CODES.get(lang, "en-IN"), "model": s.sarvam_tts_model, "output_audio_codec": "mp3"}
        if s.sarvam_tts_speaker:
            kwargs["speaker"] = s.sarvam_tts_speaker
        resp = await client.text_to_speech.convert(**kwargs)
        if not resp.audios:
            raise ProviderError("sarvam", "TTS returned no audio", retryable=True)
        raw = base64.b64decode(resp.audios[0])
        # The docs describe WAV output; convert if the codec option was ignored.
        if raw[:4] == b"RIFF":
            raw = await wav_to_mp3(raw)
        return raw

    chunks = chunk_text(text, SARVAM_MAX_CHARS)
    parts: list[bytes] = []
    with Timer() as t:
        try:
            for c in chunks:
                parts.append(await with_retries("sarvam", lambda c=c: _one(c), attempts=2, timeout=60))
        except Exception as exc:
            done_chars = sum(len(c) for c in chunks[: len(parts)])
            await refund_budget("sarvam", max(0, cost - round(done_chars * s.sarvam_tts_paise_per_1k_chars / 1000)))
            await record_usage("sarvam", "tts", units=len(text), success=False, error=str(exc))
            raise
    await record_usage("sarvam", "tts", units=len(text), latency_ms=t.ms)
    return await concat_mp3(parts)


async def google_tts(text: str, lang: str) -> bytes:
    s = get_settings()
    if not s.google_tts_api_key:
        raise ProviderNotConfigured("google_tts", "GOOGLE_TTS_API_KEY")

    async def _one(chunk: str) -> bytes:
        async with httpx.AsyncClient(timeout=30) as http:
            r = await http.post(
                "https://texttospeech.googleapis.com/v1/text:synthesize",
                params={"key": s.google_tts_api_key},
                json={
                    "input": {"text": chunk},
                    "voice": {"languageCode": LANG_CODES.get(lang, "en-IN")},
                    "audioConfig": {"audioEncoding": "MP3", "speakingRate": 0.95},
                },
            )
            r.raise_for_status()
            return base64.b64decode(r.json()["audioContent"])

    with Timer() as t:
        try:
            # Google's limit is 5000 bytes per request; Devanagari is 3 bytes/char.
            parts = [await with_retries("google_tts", lambda c=c: _one(c), attempts=3, timeout=40) for c in chunk_text(text, 1500)]
        except Exception as exc:
            await record_usage("google_tts", "tts", units=len(text), success=False, error=str(exc))
            raise
    await record_usage("google_tts", "tts", units=len(text), latency_ms=t.ms)
    return await concat_mp3(parts)


TTS_CHAIN = [("sarvam", sarvam_tts, "sarvam-bulbul"), ("google_tts", google_tts, "google-tts")]


def _voice_id(engine: str) -> str:
    s = get_settings()
    return f"{engine}:{s.sarvam_tts_model}:{s.sarvam_tts_speaker}" if engine == "sarvam-bulbul" else engine


async def synthesize(text: str, lang: str) -> TtsResult:
    """Return stored audio for this text, generating it on first use."""
    errors: list[str] = []
    any_retryable = False
    storage = get_storage()
    for name, fn, engine in TTS_CHAIN:
        cache_key = sha256_bytes(f"{text}|{lang}|{_voice_id(engine)}".encode())
        async with sessionmaker()() as s:
            hit = await s.scalar(select(AudioCache).where(AudioCache.key == cache_key))
        if hit and storage.exists(hit.audio_key):
            return TtsResult(hit.audio_key, hit.voice_note_key, hit.engine, cached=True)
        if not breaker(name).allow():
            errors.append(f"{name}: circuit open")
            any_retryable = True
            continue
        try:
            mp3 = await fn(text, lang)
        except ProviderError as exc:
            log.warning("TTS via %s failed: %s", name, exc)
            errors.append(str(exc))
            any_retryable = any_retryable or exc.retryable
            continue
        audio_key = await storage.put(f"audio/{cache_key}.mp3", mp3)
        ogg = await mp3_to_voice_note(mp3)
        voice_key = await storage.put(f"audio/{cache_key}.ogg", ogg) if ogg else None
        async with sessionmaker()() as s:
            s.add(AudioCache(key=cache_key, audio_key=audio_key, voice_note_key=voice_key, engine=engine))
            try:
                await s.commit()
            except Exception:
                await s.rollback()
        return TtsResult(audio_key, voice_key, engine)
    raise ProviderError("tts", "all TTS providers failed: " + " | ".join(errors), retryable=any_retryable)
