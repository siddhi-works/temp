"""Spend and speed guards: these keep paid API calls and request counts from creeping back up."""

import io

import pytest
from reportlab.lib.pagesizes import A4
from reportlab.pdfgen import canvas

from app.pipeline.worker import drain
from app.providers import llm
from app.providers.media import prepare_document
from app.providers.ocr import pdf_text_layer

from .conftest import NOTICE_TEXT, wa_payload
from .test_campus import publish
from .test_flow import PHONE, join_student, upload


def make_pdf(text: str, pages: int = 1) -> bytes:
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=A4)
    for _ in range(pages):
        y = 800
        for line in text.splitlines():
            c.drawString(40, y, line.encode("latin-1", "ignore").decode())
            y -= 14
        c.showPage()
    c.save()
    return buf.getvalue()


def test_clean_pdf_text_layer_skips_paid_ocr():
    doc = prepare_document(make_pdf(NOTICE_TEXT), max_pages=6, max_side=2500)
    res = pdf_text_layer(doc)
    assert res is not None and res.engine == "pdf-text"
    assert "Hall tickets will be distributed on 02.12.2026" in res.text


def test_scanned_or_empty_pdf_falls_through_to_ocr():
    blank = prepare_document(make_pdf("", pages=1), max_pages=6, max_side=2500)
    assert pdf_text_layer(blank) is None
    sparse = prepare_document(make_pdf("Notice\nSee office", pages=1), max_pages=6, max_side=2500)
    assert pdf_text_layer(sparse) is None


def test_garbled_legacy_font_text_is_rejected():
    garbled = ("(cid:12)(cid:45) " * 40) + NOTICE_TEXT
    assert pdf_text_layer(type("D", (), {"mime": "application/pdf", "data": make_pdf(garbled)})()) is None


async def test_pdf_upload_pays_for_no_ocr(client, admin, fakes):
    files = {"file": ("notice.pdf", make_pdf(NOTICE_TEXT), "application/pdf")}
    r = await client.post("/api/notices", headers=admin, files=files)
    assert r.status_code == 202
    await drain()
    detail = (await client.get(f"/api/notices/{r.json()['notice']['id']}", headers=admin)).json()
    assert detail["ocr_engine"] == "pdf-text"
    assert fakes.ocr_calls == []  # neither Sarvam nor Gemini was asked to read it


async def test_image_only_sent_to_gemini_when_ocr_is_poor():
    assert llm.needs_image("") and llm.needs_image("short text") and llm.needs_image("x" * 500 + " [unclear] ")
    assert not llm.needs_image(NOTICE_TEXT * 2)


def test_extraction_schema_has_no_duplicate_body_output():
    assert "body_markdown" not in llm.NoticeExtraction.model_json_schema()["properties"]


async def test_light_model_falls_back_when_name_is_rejected(monkeypatch):
    monkeypatch.setenv("GEMINI_LIGHT_MODEL", "gemini-lite-test")
    from app import config

    config.get_settings.cache_clear()
    calls = []

    class Models:
        async def generate_content(self, model, contents, config):
            calls.append(model)
            if model == "gemini-lite-test":
                raise RuntimeError("404 models/gemini-2.5-flash is not found")
            return type("R", (), {"text": " ok "})()

    client = type("C", (), {"aio": type("A", (), {"models": Models()})()})()
    assert await llm.light_generate(client, "hi", max_output_tokens=10) == "ok"
    assert calls == ["gemini-lite-test", "gemini-2.5-flash"]

    calls.clear()

    class Bad:
        async def generate_content(self, model, contents, config):
            calls.append(model)
            raise RuntimeError("500 server error")

    client.aio.models = Bad()
    with pytest.raises(RuntimeError):
        await llm.light_generate(client, "hi")
    assert calls == ["gemini-lite-test"]  # real errors are not retried on a second model


async def test_repeated_question_is_answered_once(client, admin, fakes, monkeypatch):
    from app.pipeline import bot

    await join_student(client, admin)
    await publish(client, admin)
    n = {"calls": 0}

    async def counting(q, ctx, lang):
        n["calls"] += 1
        return "answer"

    monkeypatch.setattr(bot, "gemini_answer", counting)
    bot._ANSWERS.clear()
    for i, q in enumerate(["When is my exam form due?", "when is my exam form due??", "WHEN IS MY EXAM FORM DUE"]):
        await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, f"q-{i}", text=q))
        await drain()
    assert n["calls"] == 1


async def test_only_languages_students_use_get_voice(client, admin, fakes):
    await join_student(client, admin)  # English student
    await upload(client, admin)
    await drain()
    await publish_current(client, admin)
    assert set(fakes.tts_calls) == {"en"}


async def publish_current(client, admin):
    nid = (await client.get("/api/review-queue", headers=admin)).json()["queue"][0]["id"]
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    assert (await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})).status_code == 200
    await drain()


def test_hosted_database_urls_get_the_async_driver():
    from app.db import normalize_db_url

    assert normalize_db_url("postgres://u:p@h:5432/db") == "postgresql+asyncpg://u:p@h:5432/db"
    assert normalize_db_url("postgresql://u:p@h:6543/db") == "postgresql+asyncpg://u:p@h:6543/db"
    assert normalize_db_url("sqlite+aiosqlite:///x.db") == "sqlite+aiosqlite:///x.db"
