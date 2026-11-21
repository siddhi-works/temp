"""OCR chain: Sarvam Document Intelligence first, Gemini vision as fallback.

Results are cached by the SHA-256 of the prepared file, so a notice forwarded by
twenty students is paid for once.
"""

from __future__ import annotations

import html
import io
import logging
import re
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import select

from ..config import get_settings
from ..db import sessionmaker
from ..models import OcrCache
from ..storage import sha256_bytes
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
from .llm import gen_config, make_client
from .media import PDF, PreparedDocument

log = logging.getLogger("suchana.ocr")

LANG_CODES = {"en": "en-IN", "mr": "mr-IN", "hi": "hi-IN"}


@dataclass
class OcrResult:
    text: str
    engine: str
    pages: int
    cached: bool = False


def _clean_output(raw: bytes, name: str) -> str:
    text = raw.decode("utf-8", errors="replace")
    if name.endswith((".html", ".htm")) or text.lstrip().startswith("<"):
        text = re.sub(r"<(br|/p|/div|/tr|/h\d|/li)\b[^>]*>", "\n", text, flags=re.I)
        text = re.sub(r"<(td|th)\b[^>]*>", " | ", text, flags=re.I)
        text = re.sub(r"<[^>]+>", "", text)
        text = html.unescape(text)
    text = re.sub(r"[ \t]+\n", "\n", text)
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def extract_sarvam_output(blob: bytes) -> str:
    """Sarvam returns either a single file or a ZIP of per-page files."""
    if blob[:2] == b"PK":
        with zipfile.ZipFile(io.BytesIO(blob)) as z:
            names = sorted(n for n in z.namelist() if n.lower().endswith((".md", ".html", ".htm", ".txt")))
            return "\n\n".join(_clean_output(z.read(n), n.lower()) for n in names)
    return _clean_output(blob, "")


async def sarvam_ocr(doc: PreparedDocument, lang: str) -> OcrResult:
    s = get_settings()
    if not s.sarvam_api_key:
        raise ProviderNotConfigured("sarvam", "SARVAM_API_KEY")
    cost = doc.pages * s.sarvam_ocr_paise_per_page
    await reserve_budget("sarvam", cost, s.sarvam_budget_paise)

    from sarvamai import AsyncSarvamAI

    client = AsyncSarvamAI(api_subscription_key=s.sarvam_api_key)

    started = False

    async def _run() -> str:
        nonlocal started
        with tempfile.TemporaryDirectory() as d:
            src = Path(d) / f"notice{doc.ext}"
            src.write_bytes(doc.data)
            job = await client.document_intelligence.create_job(language=LANG_CODES.get(lang, "mr-IN"), output_format="md")
            await job.upload_file(str(src))
            started = True  # from here on, Sarvam may bill even if a later step fails
            await job.start()
            status = await job.wait_until_complete(poll_interval=1.5, timeout=150)
            if status.job_state == "Failed":
                raise ProviderError("sarvam", f"document job failed: {getattr(status, 'error_message', '')}", retryable=False)
            out = Path(d) / "out.bin"
            await job.download_output(str(out))
            return extract_sarvam_output(out.read_bytes())

    with Timer() as t:
        try:
            # One attempt only: a retried job could be billed twice. The job runner retries later if needed.
            text = await with_retries("sarvam", _run, attempts=1, timeout=180)
        except Exception as exc:
            if not started:
                await refund_budget("sarvam", cost)
            await record_usage("sarvam", "ocr", units=doc.pages, success=False, error=str(exc))
            raise
    await record_usage("sarvam", "ocr", units=doc.pages, latency_ms=t.ms)
    if len(text.strip()) < 10:
        raise ProviderError("sarvam", "OCR returned almost no text", retryable=False)
    return OcrResult(text=text, engine="sarvam-vision", pages=doc.pages)


OCR_PROMPT = (
    "Transcribe this college notice exactly as written. Keep the original language and script "
    "(Marathi/Hindi in Devanagari, English as is). Keep every date, time, amount, room number and "
    "name character-for-character; do not translate, summarise or correct anything. Write tables as "
    "Markdown tables. If a part is unreadable write [unclear]. Output only the transcription."
)


async def gemini_ocr(doc: PreparedDocument, lang: str) -> OcrResult:
    s = get_settings()
    if not s.gemini_api_key:
        raise ProviderNotConfigured("gemini", "GEMINI_API_KEY")
    from google.genai import types

    client = make_client(s.gemini_api_key)

    async def _run() -> str:
        resp = await client.aio.models.generate_content(
            model=s.gemini_model,
            contents=[types.Part.from_bytes(data=doc.data, mime_type=doc.mime), OCR_PROMPT],
            config=gen_config(temperature=0),
        )
        return (resp.text or "").strip()

    with Timer() as t:
        try:
            text = await with_retries("gemini", _run, attempts=3, timeout=120)
        except Exception as exc:
            await record_usage("gemini", "ocr", units=doc.pages, success=False, error=str(exc))
            raise
    await record_usage("gemini", "ocr", units=doc.pages, latency_ms=t.ms)
    if len(text) < 10:
        raise ProviderError("gemini", "OCR returned almost no text", retryable=False)
    return OcrResult(text=text, engine="gemini-vision", pages=doc.pages)


def pdf_text_layer(doc: PreparedDocument) -> OcrResult | None:
    """Free and instant: PDFs exported from Word or a DTP tool already contain their text. Used only when the
    text is clearly clean; scans, legacy-font Devanagari and anything odd fall through to OCR."""
    if doc.mime != PDF or not get_settings().pdf_text_layer:
        return None
    try:
        from pypdf import PdfReader

        reader = PdfReader(io.BytesIO(doc.data))
        pages = [(p.extract_text() or "") for p in reader.pages]
    except Exception:  # noqa: BLE001
        return None
    text = "\n\n".join(p.strip() for p in pages).strip()
    if len(text) < 80 * len(pages) or any(len(p.strip()) < 40 for p in pages):
        return None  # a page with almost no text is a scan
    if "(cid:" in text or "\ufffd" in text:
        return None
    private = sum(1 for c in text if 0xE000 <= ord(c) <= 0xF8FF)
    letters = sum(1 for c in text if c.isalnum())
    if private / len(text) > 0.005 or letters / len(text) < 0.55:
        return None
    return OcrResult(text=re.sub(r"[ \t]+\n", "\n", re.sub(r"\n{3,}", "\n\n", text)), engine="pdf-text", pages=len(pages))


OCR_CHAIN = [("sarvam", sarvam_ocr), ("gemini", gemini_ocr)]


async def run_ocr(doc: PreparedDocument, lang: str = "mr", prefer: str | None = None, cache: bool = True) -> OcrResult:
    """cache=False: nothing about this file is read from or written to the database (used for students' own reads)."""
    if (layer := pdf_text_layer(doc)) is not None:
        return layer
    key = sha256_bytes(doc.data)
    if cache:
        async with sessionmaker()() as s:
            hit = await s.scalar(select(OcrCache).where(OcrCache.sha256 == key))
            if hit:
                return OcrResult(text=hit.text, engine=hit.engine, pages=hit.pages, cached=True)

    errors: list[str] = []
    any_retryable = False
    chain = sorted(OCR_CHAIN, key=lambda item: item[0] != prefer) if prefer else OCR_CHAIN  # preferred engine first
    for name, fn in chain:
        if not breaker(name).allow():
            errors.append(f"{name}: circuit open")
            any_retryable = True
            continue
        try:
            result = await fn(doc, lang)
        except ProviderError as exc:
            log.warning("OCR via %s failed: %s", name, exc)
            errors.append(str(exc))
            any_retryable = any_retryable or exc.retryable
            continue
        if cache:
            async with sessionmaker()() as s:
                s.add(OcrCache(sha256=key, engine=result.engine, text=result.text, pages=result.pages))
                try:
                    await s.commit()
                except Exception:
                    await s.rollback()
        return result
    raise ProviderError("ocr", "all OCR providers failed: " + " | ".join(errors), retryable=any_retryable)
