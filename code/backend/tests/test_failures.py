"""System failure handling: provider outages, budget, retries, crashes, bad input, bad webhooks."""

import hashlib
import hmac
import io
import json
import zipfile
from datetime import timedelta

import httpx
import pytest
from pypdf import PdfWriter
from sqlalchemy import select, update

from app.db import sessionmaker, utcnow
from app.errors import PermanentError
from app.models import Delivery, InboundMessage, Job, Student
from app.pipeline import jobs
from app.pipeline.worker import drain
from app.providers import whatsapp
from app.providers.base import (
    BudgetExceeded,
    CircuitBreaker,
    ProviderError,
    reserve_budget,
    spent_paise,
)
from app.providers.media import prepare_document
from app.providers.ocr import extract_sarvam_output, sarvam_ocr

from .conftest import notice_image, wa_payload
from .test_flow import PHONE, join_student, upload


async def test_sarvam_outage_falls_back_to_gemini_ocr(client, admin, fakes):
    fakes.sarvam_ocr_error = ProviderError("sarvam", "HTTP 503", retryable=True, status=503)
    nid = (await upload(client, admin))["notice"]["id"]
    await drain()
    d = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    assert d["status"] == "needs_review"
    assert d["ocr_engine"] == "gemini-vision"
    assert any("backup reader" in f for f in d["flags"])


async def test_total_ocr_outage_retries_then_fails_clearly_then_recovers_on_retry(client, admin, fakes):
    fakes.sarvam_ocr_error = ProviderError("sarvam", "timeout", retryable=True)
    fakes.gemini_ocr_error = ProviderError("gemini", "timeout", retryable=True)
    nid = (await upload(client, admin))["notice"]["id"]
    for _ in range(6):  # run each attempt immediately instead of waiting for backoff
        await drain()
        async with sessionmaker()() as s:
            await s.execute(update(Job).where(Job.status == "queued").values(run_after=utcnow()))
            await s.commit()
    d = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    assert d["status"] == "failed"
    assert "all OCR providers failed" in d["error_message"]
    jobs_info = (await client.get(f"/api/notices/{nid}/jobs", headers=admin)).json()
    assert jobs_info["dead"] == 1 and jobs_info["jobs"][0]["attempts"] == 5

    # Providers come back; staff press Retry.
    fakes.sarvam_ocr_error = fakes.gemini_ocr_error = None
    from app.providers.base import reset_breakers

    reset_breakers()
    assert (await client.post(f"/api/notices/{nid}/retry", headers=admin)).status_code == 200
    await drain()
    assert (await client.get(f"/api/notices/{nid}", headers=admin)).json()["status"] == "needs_review"


async def test_bad_file_fails_permanently_without_retries(client, admin, fakes):
    # Valid JPEG header but too small to read: rejected before any paid API call.
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (50, 50), "white").save(buf, format="JPEG")
    r = await client.post("/api/notices", headers=admin, files={"file": ("tiny.jpg", buf.getvalue(), "image/jpeg")})
    nid = r.json()["notice"]["id"]
    await drain()
    d = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    assert d["status"] == "failed" and d["error_code"] == "image_too_small"
    assert fakes.ocr_calls == []


async def test_upload_rejects_non_documents_and_oversize(client, admin):
    r = await client.post("/api/notices", headers=admin, files={"file": ("x.exe", b"MZ\x90\x00binary", "image/jpeg")})
    assert r.status_code == 415 and r.json()["error"]["code"] == "unsupported_file"
    r = await client.post("/api/notices", headers=admin, files={"file": ("e.pdf", b"", "application/pdf")})
    assert r.status_code == 400


async def test_llm_outage_degrades_to_rule_based_extraction(client, admin, fakes):
    fakes.extract_error = ProviderError("gemini", "HTTP 500", retryable=True, status=500)
    nid = (await upload(client, admin))["notice"]["id"]
    for _ in range(3):
        await drain()
        async with sessionmaker()() as s:
            await s.execute(update(Job).where(Job.status == "queued").values(run_after=utcnow()))
            await s.commit()
    d = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    assert d["status"] == "needs_review"
    assert d["extraction_engine"] == "rules"
    assert any(a["due_date"] == "2026-11-27" for a in d["actions"])
    assert any("pattern matching" in f for f in d["flags"])
    assert fakes.ocr_calls == ["sarvam"], "OCR result was checkpointed, not paid for again on retry"


async def test_tts_outage_still_publishes_text(client, admin, fakes):
    await join_student(client, admin)
    fakes.tts_error = ProviderError("sarvam", "budget", retryable=False)
    nid = (await upload(client, admin))["notice"]["id"]
    await drain()
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    await drain()
    d = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    assert d["status"] == "published"
    assert all(r["audio_url"] is None and r["error"] for r in d["renditions"])
    assert fakes.sent[-1][0] == "text"  # student still gets the notice


async def test_closed_window_defers_then_resends_when_student_writes(client, admin, fakes):
    await join_student(client, admin)
    async with sessionmaker()() as s:
        await s.execute(update(Student).values(last_inbound_at=utcnow() - timedelta(days=2)))
        await s.commit()
    nid = (await upload(client, admin))["notice"]["id"]
    await drain()
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    sent_before = len(fakes.sent)
    await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    await drain()
    async with sessionmaker()() as s:
        assert (await s.scalar(select(Delivery))).status == "deferred_window"
    assert len(fakes.sent) == sent_before

    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "hello-again", text="hi"))
    await drain()
    async with sessionmaker()() as s:
        assert (await s.scalar(select(Delivery))).status == "sent"
    assert any(m[0] == "audio" for m in fakes.sent[sent_before:])


async def test_template_used_outside_window_when_configured(client, admin, fakes, monkeypatch):
    monkeypatch.setenv("WHATSAPP_NOTICE_TEMPLATE", "notice_alert")
    from app.config import get_settings

    get_settings.cache_clear()
    await join_student(client, admin)
    async with sessionmaker()() as s:
        await s.execute(update(Student).values(last_inbound_at=utcnow() - timedelta(days=2)))
        await s.commit()
    nid = (await upload(client, admin))["notice"]["id"]
    await drain()
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    await drain()
    assert fakes.sent[-1][0] == "template" and fakes.sent[-1][2] == "notice_alert"


async def test_webhook_duplicates_are_ignored(client, admin, fakes):
    p = wa_payload(PHONE, "dup-1", text="HELP")
    assert (await client.post("/webhooks/whatsapp", json=p)).json()["messages"] == 1
    assert (await client.post("/webhooks/whatsapp", json=p)).json()["messages"] == 0
    await drain()
    async with sessionmaker()() as s:
        assert len((await s.scalars(select(InboundMessage))).all()) == 1
    assert len([m for m in fakes.sent if m[0] == "text"]) == 1


async def test_webhook_signature_enforced_when_secret_set(client, monkeypatch):
    monkeypatch.setenv("WHATSAPP_APP_SECRET", "s3cret")
    from app.config import get_settings

    get_settings.cache_clear()
    body = json.dumps(wa_payload(PHONE, "sig-1", text="HELP")).encode()
    bad = await client.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": "sha256=deadbeef", "content-type": "application/json"})
    assert bad.status_code == 401
    good_sig = "sha256=" + hmac.new(b"s3cret", body, hashlib.sha256).hexdigest()
    good = await client.post("/webhooks/whatsapp", content=body, headers={"x-hub-signature-256": good_sig, "content-type": "application/json"})
    assert good.status_code == 200


async def test_webhook_verification_handshake(client):
    ok = await client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "suchana-verify", "hub.challenge": "42"})
    assert ok.text == "42"
    bad = await client.get("/webhooks/whatsapp", params={"hub.mode": "subscribe", "hub.verify_token": "nope", "hub.challenge": "42"})
    assert bad.status_code == 403


async def test_crashed_worker_lease_expires_and_job_is_reclaimed(dbready):
    async with sessionmaker()() as s:
        job = await jobs.enqueue(s, "noop_test", {})
        await s.commit()
        jid = job.id
    claimed = await jobs.claim_one()
    assert claimed.id == jid
    assert await jobs.claim_one() is None  # leased
    async with sessionmaker()() as s:  # simulate the worker dying mid-job
        await s.execute(update(Job).where(Job.id == jid).values(locked_until=utcnow() - timedelta(seconds=1)))
        await s.commit()
    again = await jobs.claim_one()
    assert again.id == jid and again.attempts == 2


async def test_unknown_job_type_goes_dead_not_loop(dbready):
    async with sessionmaker()() as s:
        await jobs.enqueue(s, "does_not_exist", {})
        await s.commit()
    await drain()
    async with sessionmaker()() as s:
        assert (await s.scalar(select(Job))).status == "dead"


async def test_budget_cap_blocks_paid_calls_and_is_atomic(dbready, monkeypatch):
    monkeypatch.setenv("SARVAM_BUDGET_PAISE", "100")
    from app.config import get_settings

    get_settings.cache_clear()
    await reserve_budget("sarvam", 60, 100)
    with pytest.raises(BudgetExceeded):
        await reserve_budget("sarvam", 60, 100)
    assert await spent_paise("sarvam") == 60
    # The real Sarvam OCR function refuses before touching the network.
    doc = prepare_document(notice_image("budget"), max_pages=6, max_side=2500)
    monkeypatch.setenv("SARVAM_BUDGET_PAISE", "80")
    get_settings.cache_clear()
    with pytest.raises(BudgetExceeded):
        await sarvam_ocr(doc, "mr")


async def test_circuit_breaker_opens_and_half_opens():
    cb = CircuitBreaker("x", failure_threshold=3, cooldown_seconds=0.05)
    for _ in range(3):
        cb.failure()
    assert cb.state == "open" and not cb.allow()
    import asyncio

    await asyncio.sleep(0.06)
    assert cb.state == "half_open" and cb.allow()
    cb.success()
    assert cb.state == "closed"


def test_meta_error_mapping():
    def resp(status, code):
        return httpx.Response(status, json={"error": {"code": code, "message": "x"}})

    with pytest.raises(whatsapp.WindowClosed):
        whatsapp._raise_for_meta(resp(400, 131047))
    with pytest.raises(ProviderError) as e:
        whatsapp._raise_for_meta(resp(400, 130429))
    assert e.value.retryable
    with pytest.raises(ProviderError) as e:
        whatsapp._raise_for_meta(resp(400, 131026))
    assert not e.value.retryable
    with pytest.raises(ProviderError) as e:
        whatsapp._raise_for_meta(resp(502, None))
    assert e.value.retryable


def test_sarvam_zip_output_is_parsed_in_page_order():
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as z:
        z.writestr("page_2.md", "Second page")
        z.writestr("page_1.md", "# First page\n\n| Day | Time |\n|---|---|\n| Mon | 10:00 |")
    text = extract_sarvam_output(buf.getvalue())
    assert text.index("First page") < text.index("Second page")
    assert extract_sarvam_output(b"<p>Hello</p><table><tr><td>A</td><td>B</td></tr></table>").startswith("Hello")


def test_pdf_limits_and_corruption():
    w = PdfWriter()
    for _ in range(8):
        w.add_blank_page(width=595, height=842)
    buf = io.BytesIO()
    w.write(buf)
    with pytest.raises(PermanentError) as e:
        prepare_document(buf.getvalue(), max_pages=6, max_side=2500)
    assert e.value.code == "too_many_pages"
    with pytest.raises(PermanentError) as e:
        prepare_document(b"%PDF-1.4 garbage", max_pages=6, max_side=2500)
    assert e.value.code == "pdf_corrupt"


def test_sideways_photo_is_rotated_upright():
    from PIL import Image

    img = Image.new("RGB", (1600, 1000), "white")
    exif = Image.Exif()
    exif[0x0112] = 6  # "rotate 90" orientation tag, as phones write it
    buf = io.BytesIO()
    img.save(buf, format="JPEG", exif=exif)
    doc = prepare_document(buf.getvalue(), max_pages=6, max_side=2500)
    out = Image.open(io.BytesIO(doc.data))
    assert out.size == (1000, 1600)


async def test_staff_auth_and_isolation_between_colleges(client, admin, fakes):
    assert (await client.get("/api/notices")).status_code == 401
    assert (await client.get("/api/notices", headers={"Authorization": "Bearer wrong"})).status_code == 401
    # A verifier cannot upload or approve.
    r = await client.post("/api/staff", headers=admin, json={"name": "Rotaract Volunteer", "role": "verifier", "rotaract_club": "RAC Pune"})
    vh = {"Authorization": f"Bearer {r.json()['token']}"}
    assert (await client.post("/api/notices", headers=vh, files={"file": ("n.jpg", notice_image(), "image/jpeg")})).status_code == 403
    # Bootstrap cannot be run twice.
    again = await client.post("/api/setup/bootstrap", json={"college_code": "EVIL", "college_name": "Evil College", "admin_name": "x y"})
    assert again.status_code == 403


async def test_stop_prevents_delivery(client, admin, fakes):
    await join_student(client, admin)
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "stop-1", text="STOP"))
    await drain()
    nid = (await upload(client, admin))["notice"]["id"]
    await drain()
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    before = len(fakes.sent)
    await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    await drain()
    assert len(fakes.sent) == before
    async with sessionmaker()() as s:
        assert (await s.scalars(select(Delivery))).all() == []


async def test_expired_media_reply_is_graceful(client, admin, fakes):
    await join_student(client, admin)
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "fwd-gone", image_id="gone"))
    await drain()
    async with sessionmaker()() as s:
        msg = await s.scalar(select(InboundMessage).where(InboundMessage.wa_message_id == "fwd-gone"))
        job = await s.scalar(select(Job).where(Job.dedupe_key == "inbound:fwd-gone"))
    assert job.status == "done" and msg.status == "done"  # handled, no retry loop
    assert "could not open that file" in fakes.sent[-1][2]  # the student is never left hanging


def test_llm_times_are_normalised():
    from app.pipeline.steps import _hhmm

    assert _hhmm("10.30") == "10:30"
    assert _hhmm("2 pm") == "14:00"
    assert _hhmm("12:15 a.m.") == "00:15"
    assert _hhmm("noon") is None and _hhmm("25:00") is None



async def test_event_bus_isolates_colleges_and_drops_for_slow_clients():
    from app.events import EventBus

    bus = EventBus()
    a, b = bus.subscribe("college-a"), bus.subscribe("college-b")
    bus.publish("notice.updated", {"id": 1}, college_id="college-a")
    assert a.queue.qsize() == 1 and b.queue.qsize() == 0
    for i in range(300):  # a stuck browser tab must not block the pipeline
        bus.publish("x", {"i": i}, college_id="college-a")
    assert a.queue.qsize() == 200
    bus.unsubscribe(a)
    assert bus.subscriber_count == 1
