"""File inspection and normalisation before any paid API sees it.

Phone photos arrive rotated, huge, or as the wrong type. Fixing that here makes
OCR more accurate and cheaper, and rejects junk before it costs money.
"""

from __future__ import annotations

import asyncio
import io
import logging
import shutil
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path

from ..errors import PermanentError

log = logging.getLogger("suchana.media")

PDF = "application/pdf"
JPEG = "image/jpeg"
PNG = "image/png"
WEBP = "image/webp"


def sniff_mime(data: bytes) -> str | None:
    """Detect type from magic bytes; never trust the client's content-type."""
    if data[:5] == b"%PDF-":
        return PDF
    if data[:3] == b"\xff\xd8\xff":
        return JPEG
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return PNG
    if data[:4] == b"RIFF" and data[8:12] == b"WEBP":
        return WEBP
    return None


@dataclass
class PreparedDocument:
    data: bytes
    mime: str
    pages: int
    ext: str


def prepare_document(data: bytes, *, max_pages: int, max_side: int) -> PreparedDocument:
    mime = sniff_mime(data)
    if mime is None:
        raise PermanentError(
            "This file is not a PDF or photo. Please send a PDF, JPG or PNG.", code="unsupported_file"
        )
    if mime == PDF:
        from pypdf import PdfReader

        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                try:
                    reader.decrypt("")
                except Exception as exc:
                    raise PermanentError("This PDF is password protected.", code="pdf_encrypted") from exc
            pages = len(reader.pages)
        except PermanentError:
            raise
        except Exception as exc:
            raise PermanentError("This PDF is damaged and cannot be opened.", code="pdf_corrupt") from exc
        if pages == 0:
            raise PermanentError("This PDF has no pages.", code="pdf_empty")
        if pages > max_pages:
            raise PermanentError(
                f"This PDF has {pages} pages. Notices up to {max_pages} pages are supported; split longer files.",
                code="too_many_pages",
            )
        return PreparedDocument(data, PDF, pages, ".pdf")

    from PIL import Image, ImageOps

    try:
        img = Image.open(io.BytesIO(data))
        img = ImageOps.exif_transpose(img)  # fix sideways phone photos
        if img.mode not in ("RGB", "L"):
            img = img.convert("RGB")
        w, h = img.size
        if min(w, h) < 200:
            raise PermanentError("This image is too small to read. Please send a clearer photo.", code="image_too_small")
        scale = max_side / max(w, h)
        if scale < 1:
            img = img.resize((int(w * scale), int(h * scale)), Image.LANCZOS)
        out = io.BytesIO()
        img.save(out, format="JPEG", quality=90, optimize=True)
        return PreparedDocument(out.getvalue(), JPEG, 1, ".jpg")
    except PermanentError:
        raise
    except Exception as exc:
        raise PermanentError("This image could not be opened.", code="image_corrupt") from exc


async def mp3_to_voice_note(mp3: bytes) -> bytes | None:
    """Convert to OGG/Opus, the format WhatsApp plays as a voice note.

    Returns None if ffmpeg is missing or fails; callers then send the MP3 as a
    regular audio message, which WhatsApp also supports.
    """
    if not shutil.which("ffmpeg"):
        return None

    def _run() -> bytes | None:
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "in.mp3", Path(d) / "out.ogg"
            src.write_bytes(mp3)
            proc = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-c:a", "libopus", "-b:a", "32k", "-ac", "1", str(dst)],
                capture_output=True,
                timeout=60,
            )
            if proc.returncode != 0 or not dst.exists():
                log.warning("ffmpeg opus conversion failed: %s", proc.stderr[:300])
                return None
            return dst.read_bytes()

    return await asyncio.to_thread(_run)


async def concat_mp3(parts: list[bytes]) -> bytes:
    """Join TTS chunks. MP3 frames concatenate safely; ffmpeg re-muxes when available."""
    if len(parts) == 1:
        return parts[0]
    if not shutil.which("ffmpeg"):
        return b"".join(parts)

    def _run() -> bytes:
        with tempfile.TemporaryDirectory() as d:
            listing = Path(d) / "list.txt"
            names = []
            for i, p in enumerate(parts):
                f = Path(d) / f"p{i}.mp3"
                f.write_bytes(p)
                names.append(f"file '{f}'")
            listing.write_text("\n".join(names))
            out = Path(d) / "out.mp3"
            proc = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-f", "concat", "-safe", "0", "-i", str(listing), "-c", "copy", str(out)],
                capture_output=True,
                timeout=60,
            )
            return out.read_bytes() if proc.returncode == 0 and out.exists() else b"".join(parts)

    return await asyncio.to_thread(_run)


async def wav_to_mp3(wav: bytes) -> bytes:
    if not shutil.which("ffmpeg"):
        raise RuntimeError("ffmpeg is required to convert WAV audio")

    def _run() -> bytes:
        with tempfile.TemporaryDirectory() as d:
            src, dst = Path(d) / "in.wav", Path(d) / "out.mp3"
            src.write_bytes(wav)
            proc = subprocess.run(
                ["ffmpeg", "-y", "-loglevel", "error", "-i", str(src), "-b:a", "64k", str(dst)], capture_output=True, timeout=60
            )
            if proc.returncode != 0:
                raise RuntimeError(f"ffmpeg failed: {proc.stderr[:200]!r}")
            return dst.read_bytes()

    return await asyncio.to_thread(_run)


def sniff_audio(data: bytes) -> tuple[str, str] | None:
    """(mime, extension) from magic bytes for voice recordings made by phone browsers."""
    if data[:4] == b"OggS":
        return "audio/ogg", ".ogg"
    if data[:4] == b"\x1a\x45\xdf\xa3":
        return "audio/webm", ".webm"
    if data[4:8] == b"ftyp":
        return "audio/mp4", ".m4a"
    if data[:3] == b"ID3" or data[:2] in (b"\xff\xfb", b"\xff\xf3", b"\xff\xf2"):
        return "audio/mpeg", ".mp3"
    if data[:4] == b"RIFF" and data[8:12] == b"WAVE":
        return "audio/wav", ".wav"
    return None
