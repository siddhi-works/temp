"""Test setup.

External services (Sarvam, Gemini, Google TTS, WhatsApp) are replaced with
in-process fakes so tests are deterministic and free. Everything else (database,
job queue, verification, routing, webhooks, storage, ffmpeg) is real.
"""

from __future__ import annotations

import io
import os
from dataclasses import dataclass, field

import pytest
import pytest_asyncio
from cryptography.fernet import Fernet
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw


@pytest.fixture(autouse=True)
def env(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)  # tests must never read the developer's real .env
    monkeypatch.setenv("DATABASE_URL", f"sqlite+aiosqlite:///{tmp_path}/test.db")
    monkeypatch.setenv("STORAGE_DIR", str(tmp_path / "storage"))
    monkeypatch.setenv("WORKER_ENABLED", "false")
    monkeypatch.setenv("SECRET_KEY", "test-secret")
    monkeypatch.setenv("PHONE_ENCRYPTION_KEY", Fernet.generate_key().decode())
    monkeypatch.setenv("SARVAM_API_KEY", "test")
    monkeypatch.setenv("GEMINI_API_KEY", "test")
    monkeypatch.setenv("WHATSAPP_TOKEN", "test")
    monkeypatch.setenv("WHATSAPP_PHONE_NUMBER_ID", "123")
    monkeypatch.setenv("FRONTEND_BASE_URL", "https://app.example")
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")  # the WhatsApp tests need it; one test proves it stays off by default
    monkeypatch.setenv("COLLEGE_MODE", "true")  # most tests exercise the college side too; one test proves it is off by default
    for k in ("WHATSAPP_APP_SECRET", "WHATSAPP_NOTICE_TEMPLATE", "GOOGLE_TTS_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    from app import config, storage
    from app.providers import base

    config.get_settings.cache_clear()
    storage.reset_storage()
    base.reset_breakers()
    yield
    config.get_settings.cache_clear()


@pytest_asyncio.fixture
async def dbready():
    from app.db import dispose_engine, init_models

    await init_models()
    yield
    await dispose_engine()


# ---------------------------------------------------------------------------------
# Realistic notice text (what OCR would return for a bilingual college notice)
# ---------------------------------------------------------------------------------

NOTICE_TEXT = """MODERN COLLEGE OF ARTS, SCIENCE AND COMMERCE, PUNE
Examination Section
NOTICE
Ref: EXAM/2026/114                                  Date: 20/11/2026

परीक्षा अर्ज भरण्याबाबत सूचना
सर्व एफ.वाय. बी.कॉम विद्यार्थ्यांना कळविण्यात येते की हिवाळी सत्र परीक्षा अर्ज भरण्याची अंतिम तारीख
२७ नोव्हेंबर २०२६ (गुरुवार) आहे. परीक्षा शुल्क Rs. 500/- कार्यालयात जमा करावे.
Last date for exam form submission: 27/11/2026 (Thursday).
Hall tickets will be distributed on 02.12.2026 at 10.30 am in Room 14.

Principal
"""

NOTICE_V2_TEXT = NOTICE_TEXT.replace("२७ नोव्हेंबर २०२६ (गुरुवार)", "२९ नोव्हेंबर २०२६ (रविवार)").replace(
    "27/11/2026 (Thursday)", "29/11/2026 (Sunday)"
).replace("Ref: EXAM/2026/114", "Ref: EXAM/2026/119 (Revised)")


def extraction_for(text: str, last_date: str, weekday_in_quote: str) -> dict:
    """What Gemini returns for the notice above (shape enforced by the JSON schema)."""
    return {
        "is_notice": True,
        "title": "Last date for winter exam form submission",
        "issuer": "Examination Section",
        "category": "exam",
        "issue_date": "2026-11-20",
        "audience": "FY B.Com students",
        "language": "mixed",
        "legibility": "good",
        "actions": [
            {
                "kind": "deadline",
                "label": "Submit exam form and pay fee",
                "due_date": last_date,
                "due_time": None,
                "amount": "Rs. 500/-",
                "venue": "College office",
                "source_quote": f"Last date for exam form submission: {weekday_in_quote}.",
            },
            {
                "kind": "event",
                "label": "Collect hall ticket",
                "due_date": "2026-12-02",
                "due_time": "10:30",
                "amount": None,
                "venue": "Room 14",
                "source_quote": "Hall tickets will be distributed on 02.12.2026 at 10.30 am in Room 14.",
            },
        ],
        "timetable": [],
        "summaries": {
            "en": "Submit your winter exam form and pay 500 rupees at the college office by the last date. Collect your hall ticket on Wednesday, 2 December at 10:30 in Room 14.",
            "mr": "हिवाळी परीक्षा अर्ज भरा आणि ५०० रुपये शुल्क कार्यालयात जमा करा. हॉल तिकीट २ डिसेंबर रोजी सकाळी १०.३० वाजता खोली १४ मध्ये मिळेल.",
            "hi": "शीतकालीन परीक्षा फॉर्म भरें और 500 रुपये शुल्क जमा करें. हॉल टिकट 2 दिसंबर को सुबह 10.30 बजे कमरा 14 में मिलेगा.",
        },
        "body_markdown": "# Notice\n\n" + text,
        "reviewer_notes": [],
    }


def notice_image(label: str = "notice") -> bytes:
    """A real JPEG (with text drawn on it) so media checks run on actual image bytes."""
    img = Image.new("RGB", (1200, 1600), "white")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(NOTICE_TEXT.splitlines()):
        d.text((60, 60 + i * 40), line.encode("ascii", "ignore").decode(), fill="black")
    d.text((60, 1500), label, fill="black")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ---------------------------------------------------------------------------------
# Fakes for external services
# ---------------------------------------------------------------------------------


@dataclass
class Fakes:
    current_text: str = NOTICE_TEXT
    ocr_calls: list = field(default_factory=list)
    extract_calls: int = 0
    extraction: dict | None = None
    extract_error: Exception | None = None
    sarvam_ocr_error: Exception | None = None
    gemini_ocr_error: Exception | None = None
    tts_error: Exception | None = None
    tts_calls: list = field(default_factory=list)
    sent: list = field(default_factory=list)
    window_closed: bool = False
    media: dict = field(default_factory=dict)
    answer: str = "Your exam form is due on Thursday, 27 November."
    counter: int = 0

    def next_id(self) -> str:
        self.counter += 1
        return f"wamid.{self.counter}"


@pytest.fixture
def fakes(monkeypatch):
    from app.pipeline import bot, steps
    from app.providers import ocr, tts, whatsapp
    from app.providers.base import ProviderError
    from app.providers.ocr import OcrResult

    f = Fakes()

    async def fake_sarvam(doc, lang):
        f.ocr_calls.append("sarvam")
        if f.sarvam_ocr_error:
            raise f.sarvam_ocr_error
        return OcrResult(text=f.current_text, engine="sarvam-vision", pages=doc.pages)

    async def fake_gemini_ocr(doc, lang):
        f.ocr_calls.append("gemini")
        if f.gemini_ocr_error:
            raise f.gemini_ocr_error
        return OcrResult(text=f.current_text, engine="gemini-vision", pages=doc.pages)

    monkeypatch.setattr(ocr, "OCR_CHAIN", [("sarvam", fake_sarvam), ("gemini", fake_gemini_ocr)])

    async def fake_extract(text, *, image, image_mime, today):
        f.extract_calls += 1
        if f.extract_error:
            raise f.extract_error
        from app.providers.llm import NoticeExtraction

        return NoticeExtraction.model_validate(f.extraction or extraction_for(text, "2026-11-27", "27/11/2026 (Thursday)"))

    monkeypatch.setattr(steps, "gemini_extract", fake_extract)

    async def fake_tts(text, lang):
        f.tts_calls.append(lang)
        if f.tts_error:
            raise f.tts_error
        # Real MP3 bytes generated by ffmpeg so the opus conversion path is exercised.
        return _silent_mp3()

    monkeypatch.setattr(tts, "TTS_CHAIN", [("sarvam", fake_tts, "sarvam-bulbul")])

    async def send_text(to, text, preview_url=True):
        if f.window_closed:
            raise whatsapp.WindowClosed()
        f.sent.append(("text", to, text))
        return whatsapp.SentMessage(f.next_id())

    async def upload_media(data, mime, filename):
        if f.window_closed:
            raise whatsapp.WindowClosed()
        return f"media-{len(data)}-{mime}"

    async def send_audio(to, media_id):
        if f.window_closed:
            raise whatsapp.WindowClosed()
        f.sent.append(("audio", to, media_id))
        return whatsapp.SentMessage(f.next_id())

    async def send_template(to, name, lang, params):
        f.sent.append(("template", to, name, params))
        return whatsapp.SentMessage(f.next_id())

    async def download_media(media_id):
        if media_id not in f.media:
            raise ProviderError("whatsapp", "media expired", retryable=False)
        return f.media[media_id]

    async def mark_read(mid):
        return None

    for name, fn in dict(send_text=send_text, upload_media=upload_media, send_audio=send_audio, send_template=send_template, download_media=download_media, mark_read=mark_read).items():
        monkeypatch.setattr(whatsapp, name, fn)

    async def fake_answer(q, ctx, lang):
        return f.answer

    monkeypatch.setattr(bot, "gemini_answer", fake_answer)
    return f


_MP3: bytes | None = None


def _silent_mp3() -> bytes:
    global _MP3
    if _MP3 is None:
        import subprocess
        import tempfile

        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "s.mp3")
            subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "anullsrc=r=22050:cl=mono", "-t", "1", out], check=True)
            _MP3 = open(out, "rb").read()
    return _MP3


@pytest_asyncio.fixture
async def client(dbready):
    from app.main import create_app

    app = create_app()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


@pytest_asyncio.fixture
async def admin(client):
    r = await client.post(
        "/api/setup/bootstrap",
        json={"college_code": "MODERN", "college_name": "Modern College, Pune", "admin_name": "Exam Section Head", "admin_office": "Exam Section"},
    )
    assert r.status_code == 201, r.text
    return {"Authorization": f"Bearer {r.json()['admin_token']}"}


def wa_payload(phone: str, msg_id: str, *, text: str | None = None, image_id: str | None = None, audio_id: str | None = None, ts: int | None = None) -> dict:
    import time

    msg = {"from": phone, "id": msg_id, "timestamp": str(ts or int(time.time()))}
    if text is not None:
        msg.update(type="text", text={"body": text})
    elif image_id:
        msg.update(type="image", image={"id": image_id, "mime_type": "image/jpeg"})
    elif audio_id:
        msg.update(type="audio", audio={"id": audio_id, "mime_type": "audio/ogg; codecs=opus"})
    return {"object": "whatsapp_business_account", "entry": [{"id": "x", "changes": [{"field": "messages", "value": {"messages": [msg]}}]}]}


def wa_status(message_id: str, status: str) -> dict:
    return {"entry": [{"changes": [{"value": {"statuses": [{"id": message_id, "status": status}]}}]}]}


@pytest_asyncio.fixture
async def engine_for_safety(client, monkeypatch):
    """A web student plus the reading engine with its AI faked, for a notice that asks for a payment to a UPI ID."""
    from datetime import date, timedelta
    from types import SimpleNamespace

    from app.pipeline import reader
    from app.providers.llm import NoticeExtraction
    from app.providers.ocr import OcrResult
    from app.routers import public

    public._hits.clear()
    due, hall = date.today() + timedelta(days=10), date.today() + timedelta(days=14)
    text = (
        f"NOTICE\nLast date for exam form submission: {due:%d/%m/%Y} ({due:%A}).\n"
        f"Hall tickets will be distributed on {hall:%d.%m.%Y} at 10.30 am in Room 14.\n"
        "Pay the exam fee Rs. 500 to the UPI ID asha.college@ybl.\n"
    )
    ex = extraction_for(text, due.isoformat(), f"{due:%d/%m/%Y} ({due:%A})")
    ex["actions"][1].update(due_date=hall.isoformat(), source_quote=f"Hall tickets will be distributed on {hall:%d.%m.%Y} at 10.30 am in Room 14.")
    ex["summaries"] = {k: "Submit your winter exam form." for k in ("en", "mr", "hi")}
    st = SimpleNamespace(due=due, extraction=ex)

    async def fake_ocr(doc, lang="mr", prefer=None, cache=True):
        return OcrResult(text=text, engine="fake", pages=1)

    async def fake_extract(ocr_text, **kw):
        return NoticeExtraction.model_validate(st.extraction)

    async def fake_speak(t, lang):
        return b"mp3"

    monkeypatch.setattr(reader, "run_ocr", fake_ocr)
    monkeypatch.setattr(reader, "gemini_extract", fake_extract)
    monkeypatch.setattr(reader, "speak_uncached", fake_speak)
    tok = (await client.post("/api/public/join", json={"lang": "en"})).json()["token"]
    return st, tok
