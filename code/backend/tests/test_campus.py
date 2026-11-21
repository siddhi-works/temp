"""Saathi Campus features end to end: real HTTP API, database, job queue and WhatsApp webhook.
External AI/WhatsApp/push services are fakes (see conftest)."""

from __future__ import annotations

import io
from datetime import timedelta

import pytest
from PIL import Image, ImageDraw
from sqlalchemy import select

from app.db import sessionmaker
from app.models import Delivery, PersonalDeadline, Reminder, Snap, Student
from app.pipeline.worker import drain
from app.security import sign_student_token

from .conftest import wa_payload
from .test_flow import PHONE, join_student, upload

BOARD_TEXT = """Thermodynamics - Lecture 7
First law: dU = Q - W
Carnot efficiency = 1 - Tc/Th
Assignment 3: problems 4 to 9"""

# The hall-ticket event in the test notice is Wednesday 2 Dec 2026, 10:30, Room 14.
WED_CLASS = {"weekday": 2, "start": "10:00", "end": "11:00", "subject": "Financial Accounting", "room": "12"}
THU_CLASS = {"weekday": 3, "start": "09:00", "end": "10:00", "subject": "Economics", "room": "7"}


@pytest.fixture
def pushes(monkeypatch):
    """Turn on Web Push with real VAPID keys, capture what would be sent."""
    from app import config
    from app.providers import webpush

    priv, pub = webpush.generate_vapid_keys()
    monkeypatch.setenv("VAPID_PRIVATE_KEY", priv)
    monkeypatch.setenv("VAPID_PUBLIC_KEY", pub)
    config.get_settings.cache_clear()
    sent: list[dict] = []

    async def fake_send(endpoint, p256dh, auth, payload):
        if "gone" in endpoint:
            from app.providers.base import ProviderError

            raise ProviderError("webpush", "gone", retryable=False, status=410)
        sent.append({"endpoint": endpoint, **payload})

    monkeypatch.setattr(webpush, "send_push", fake_send)
    return sent


@pytest.fixture
def no_ai(monkeypatch):
    """Gemini label/memory answers unavailable: the features must still work on fallbacks."""
    from app.pipeline import campus
    from app.providers.base import ProviderError

    async def down(*a, **k):
        raise ProviderError("gemini", "unavailable", retryable=True)

    monkeypatch.setattr(campus, "gemini_label", down)
    monkeypatch.setattr(campus, "gemini_memory_answer", down)


async def student_token() -> str:
    async with sessionmaker()() as s:
        st = await s.scalar(select(Student))
        return sign_student_token(st.id)


async def publish(client, admin):
    await upload(client, admin)
    await drain()
    nid = (await client.get("/api/review-queue", headers=admin)).json()["queue"][0]["id"]
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    r = await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    assert r.status_code == 200, r.text
    await drain()
    return nid


def board_photo() -> bytes:
    img = Image.new("RGB", (1000, 700), "black")
    d = ImageDraw.Draw(img)
    for i, line in enumerate(BOARD_TEXT.splitlines()):
        d.text((40, 40 + i * 50), line, fill="white")
    buf = io.BytesIO()
    img.save(buf, format="JPEG")
    return buf.getvalue()


# ------------------------------------------------------------------------------ Clash Radar


async def test_clash_radar_alerts_only_the_affected_student(client, admin, fakes, pushes):
    await join_student(client, admin)
    await join_student(client, admin, phone="919800000002", msg_id="in-2")
    async with sessionmaker()() as s:
        a, b = (await s.scalars(select(Student).order_by(Student.created_at))).all()
        ta, tb = sign_student_token(a.id), sign_student_token(b.id)

    assert (await client.put("/api/me/timetable", params={"t": ta}, json=[WED_CLASS])).status_code == 200
    assert (await client.put("/api/me/timetable", params={"t": tb}, json=[THU_CLASS])).status_code == 200
    r = await client.post("/api/me/push/subscribe", params={"t": ta}, json={"endpoint": "https://push.example/a", "keys": {"p256dh": "x", "auth": "y"}})
    assert r.status_code == 201

    await publish(client, admin)

    texts = [m for m in fakes.sent if m[0] == "text"]
    clash = [m for m in texts if "Financial Accounting" in m[2]]
    assert len(clash) == 1, "exactly one clash alert"
    assert clash[0][1] == PHONE  # student A only; B's Thursday class does not clash
    assert "Collect hall ticket" in clash[0][2]
    assert not any("Economics" in m[2] for m in texts)
    # Clash alert vibrates with the clash pattern; the notice itself got its own buzz.
    kinds = [p["type"] for p in pushes]
    assert "clash" in kinds and kinds.count("clash") == 1
    clash_push = next(p for p in pushes if p["type"] == "clash")
    assert clash_push["vibrate"] == [90, 70, 90, 70, 90, 70, 90]

    # Re-running the job never alerts twice.
    from app.pipeline.jobs import enqueue

    async with sessionmaker()() as s:
        d = await s.scalar(select(Delivery).where(Delivery.kind == "clash"))
        assert d.status == "sent"
        await enqueue(s, "personalize_notice", {"notice_id": d.notice_id})
        await s.commit()
    before = len(fakes.sent)
    await drain()
    assert len(fakes.sent) == before

    # Impact numbers count notices reached, not clash alerts.
    impact = (await client.get("/api/impact", headers=admin)).json()
    assert impact["deliveries"]["reached"] == 2


async def test_timetable_rejects_bad_slots_and_right_now_works(client, admin, fakes):
    await join_student(client, admin)
    t = await student_token()
    bad = dict(WED_CLASS, start="11:00", end="10:00")
    r = await client.put("/api/me/timetable", params={"t": t}, json=[bad])
    assert r.status_code == 400 and r.json()["error"]["code"] == "bad_slot"
    r = await client.put("/api/me/timetable", params={"t": t}, json=[dict(WED_CLASS, start="25:00")])
    assert r.status_code == 422

    empty = (await client.get("/api/me/now", params={"t": t})).json()
    assert empty["has_timetable"] is False and empty["text"]

    every_day = [dict(WED_CLASS, weekday=d, start="00:00", end="23:59") for d in range(7)]
    await client.put("/api/me/timetable", params={"t": t}, json=every_day)
    now = (await client.get("/api/me/now", params={"t": t})).json()
    assert now["current"]["subject"] == "Financial Accounting"
    assert "Financial Accounting" in now["text"]

    # Same answer over WhatsApp.
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "now-1", text="NOW"))
    await drain()
    assert "Financial Accounting" in fakes.sent[-1][2]

    # Bad token is refused.
    assert (await client.get("/api/me/now", params={"t": t + "x"})).status_code == 401


# ------------------------------------------------------------------------------ Verbal Deadline Catcher


async def test_catcher_text_flow_with_readback_and_reminders(client, admin, fakes, no_ai):
    await join_student(client, admin)
    t = await student_token()
    r = await client.post("/api/me/catch", params={"t": t}, data={"text": "Sir said submit the lab journal in 10 days"})
    assert r.status_code == 201, r.text
    body = r.json()
    from app.pipeline.campus import local_now

    expected = local_now().date() + timedelta(days=10)
    assert body["due_date"] == expected.isoformat()
    assert body["status"] == "pending"
    assert "YES" in body["readback"]
    assert body["label"].lower().startswith("submit the lab journal")  # fallback label strips "Sir said"

    # Nothing is scheduled until the student confirms.
    async with sessionmaker()() as s:
        assert not (await s.scalars(select(Reminder).where(Reminder.kind == "personal"))).all()

    r = await client.post(f"/api/me/catch/{body['id']}/confirm", params={"t": t})
    assert r.status_code == 200 and r.json()["reminders"] >= 1
    assert "Saved" in r.json()["message"]
    dl = (await client.get("/api/me/deadlines", params={"t": t})).json()["deadlines"]
    assert [d["id"] for d in dl] == [body["id"]]

    # Confirming twice does not double the reminders.
    await client.post(f"/api/me/catch/{body['id']}/confirm", params={"t": t})
    async with sessionmaker()() as s:
        n = len((await s.scalars(select(Reminder).where(Reminder.personal_id == body["id"]))).all())
    assert n == r.json()["reminders"]

    # Cancel removes pending reminders.
    await client.post(f"/api/me/catch/{body['id']}/cancel", params={"t": t})
    async with sessionmaker()() as s:
        assert not (await s.scalars(select(Reminder).where(Reminder.personal_id == body["id"], Reminder.status == "pending"))).all()


async def test_catcher_never_guesses_a_missing_date(client, admin, fakes, no_ai):
    await join_student(client, admin)
    t = await student_token()
    body = (await client.post("/api/me/catch", params={"t": t}, data={"text": "bring your project file"})).json()
    assert body["due_date"] is None and body["needs_date"] is True
    r = await client.post(f"/api/me/catch/{body['id']}/confirm", params={"t": t})
    assert r.status_code == 400 and r.json()["error"]["code"] == "date_required"


async def test_catcher_over_whatsapp_voice_then_yes(client, admin, fakes, no_ai, monkeypatch):
    from app.pipeline import bot

    async def fake_stt(data, mime):
        return "Assignment 3 due in 5 days"

    monkeypatch.setattr(bot, "transcribe", fake_stt)
    await join_student(client, admin)
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "c-1", text="CATCH"))
    await drain()
    assert "Ready" in fakes.sent[-1][2]

    fakes.media["voice-1"] = (b"OggS" + b"\0" * 2000, "audio/ogg")
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "c-2", audio_id="voice-1"))
    await drain()
    assert "Assignment 3" in fakes.sent[-1][2] and "YES" in fakes.sent[-1][2]

    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "c-3", text="हो"))
    await drain()
    assert fakes.sent[-1][2].startswith("Saved: Assignment 3")
    async with sessionmaker()() as s:
        pd = await s.scalar(select(PersonalDeadline))
        assert pd.status == "confirmed" and pd.source == "voice"
        st = await s.scalar(select(Student))
        assert st.catch_until is None  # armed only for one message

    # A later voice note is a normal question again, not another catch.
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "c-4", text="yes"))
    await drain()
    assert "Nothing is waiting" in fakes.sent[-1][2]


async def test_whatsapp_catch_needs_date_then_date_fix(client, admin, fakes, no_ai):
    await join_student(client, admin)
    for i, msg in enumerate(["CATCH", "bring your project file"]):
        await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, f"f-{i}", text=msg))
        await drain()
    assert "could not tell the date" in fakes.sent[-1][2]
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "f-9", text="in 3 days"))
    await drain()
    assert "YES" in fakes.sent[-1][2]
    async with sessionmaker()() as s:
        pd = await s.scalar(select(PersonalDeadline))
        assert pd.due_date is not None and pd.needs_date is False
        assert pd.label.startswith("bring your project file")


# ------------------------------------------------------------------------------ Board Snap & memory


async def test_board_snap_and_memory_search(client, admin, fakes, no_ai):
    await join_student(client, admin)
    t = await student_token()
    fakes.current_text = BOARD_TEXT
    r = await client.post("/api/me/snaps", params={"t": t}, files={"file": ("b.jpg", board_photo(), "image/jpeg")}, data={"subject": "Physics"})
    assert r.status_code == 202 and r.json()["status"] == "processing"
    await drain()
    snaps = (await client.get("/api/me/snaps", params={"t": t})).json()["snaps"]
    assert snaps[0]["status"] == "ready" and "Thermodynamics" in snaps[0]["summary"]
    assert fakes.ocr_calls[-1] == "gemini"  # boards go to Gemini first, saving Sarvam credit

    found = (await client.get("/api/me/memory", params={"t": t, "q": "carnot efficiency formula"})).json()
    assert found["found"] is True and "Tc/Th" in found["text"]
    missing = (await client.get("/api/me/memory", params={"t": t, "q": "organic chemistry benzene"})).json()
    assert missing["found"] is False

    # Not a photo: refused before any paid call.
    r = await client.post("/api/me/snaps", params={"t": t}, files={"file": ("x.txt", b"hello there", "text/plain")})
    assert r.status_code == 415

    # Another student cannot read it.
    await join_student(client, admin, phone="919800000003", msg_id="in-3")
    async with sessionmaker()() as s:
        other = (await s.scalars(select(Student).order_by(Student.created_at))).all()[-1]
    r = await client.get(f"/api/me/snaps/{snaps[0]['id']}", params={"t": sign_student_token(other.id)})
    assert r.status_code == 404

    r = await client.delete(f"/api/me/snaps/{snaps[0]['id']}", params={"t": t})
    assert r.status_code == 200
    assert (await client.get("/api/me/snaps", params={"t": t})).json()["snaps"] == []


async def test_board_snap_over_whatsapp_and_memory_first_answers(client, admin, fakes, no_ai):
    await join_student(client, admin)
    fakes.current_text = BOARD_TEXT
    fakes.media["img-1"] = (board_photo(), "image/jpeg")
    payload = wa_payload(PHONE, "s-1", image_id="img-1")
    payload["entry"][0]["changes"][0]["value"]["messages"][0]["image"]["caption"] = "board"
    await client.post("/webhooks/whatsapp", json=payload)
    await drain()
    assert "Saved" in fakes.sent[-1][2]
    async with sessionmaker()() as s:
        assert (await s.scalar(select(Snap))).status == "ready"

    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "s-2", text="what was the carnot efficiency"))
    await drain()
    assert "Tc/Th" in fakes.sent[-1][2] and "saved board" in fakes.sent[-1][2]


async def test_snap_ocr_failure_marks_snap_failed(client, admin, fakes, no_ai):
    from app.providers.base import ProviderError

    await join_student(client, admin)
    t = await student_token()
    fakes.sarvam_ocr_error = fakes.gemini_ocr_error = ProviderError("x", "down", retryable=False)
    await client.post("/api/me/snaps", params={"t": t}, files={"file": ("b.jpg", board_photo(), "image/jpeg")})
    await drain()
    snap = (await client.get("/api/me/snaps", params={"t": t})).json()["snaps"][0]
    assert snap["status"] == "failed" and snap["error"]


# ------------------------------------------------------------------------------ Exam Eve Pack


async def test_exam_eve_pack_has_checklist_and_route(client, admin, fakes, monkeypatch):
    from app.pipeline import steps

    ext = steps_extraction_with_exam()
    fakes.extraction = ext
    await join_student(client, admin)

    # A published route to the exam hall exists.
    r = await client.post("/api/routes", headers=admin, json={"title": "Gate to Exam Hall", "from_place": "Main gate", "to_place": "Exam Hall A"})
    rid = r.json()["id"]
    await client.post(f"/api/routes/{rid}/steps", headers=admin, data={"text": "Walk straight 40 steps."})
    await client.post(f"/api/routes/{rid}/publish", headers=admin)  # admin may approve own route

    await publish(client, admin)
    async with sessionmaker()() as s:
        eve = await s.scalar(select(Reminder).where(Reminder.kind == "exam_eve"))
        assert eve is not None
    await steps.send_reminder(type("J", (), {"payload": {"reminder_id": eve.id}})())
    text = fakes.sent[-1][2]
    assert "hall ticket" in text.lower() and "college ID card" in text
    assert f"/r/{rid}" in text
    async with sessionmaker()() as s:
        assert (await s.get(Reminder, eve.id)).status == "sent"


def steps_extraction_with_exam() -> dict:
    from .conftest import NOTICE_TEXT, extraction_for

    ext = extraction_for(NOTICE_TEXT, "2026-11-27", "27/11/2026 (Thursday)")
    ext["actions"][1].update(kind="exam", label="Accountancy paper", venue="Exam Hall A")
    return ext


# ------------------------------------------------------------------------------ Campus Walkthrough


async def test_routes_need_a_second_person_and_are_hidden_until_published(client, admin, fakes):
    r = await client.post("/api/staff", headers=admin, json={"name": "Rotaractor Asha", "role": "verifier", "rotaract_club": "RAC Pune"})
    assert r.status_code == 201, r.text
    vol = {"Authorization": f"Bearer {r.json()['token']}"}

    r = await client.post("/api/routes", headers=vol, json={"title": "Library to Canteen", "from_place": "Library", "to_place": "Canteen"})
    assert r.status_code == 201
    rid = r.json()["id"]

    clip = b"OggS" + b"\0" * 500
    r = await client.post(f"/api/routes/{rid}/steps", headers=vol, files={"audio": ("s.ogg", clip, "audio/ogg")}, data={"text": "Exit library, turn left."})
    assert r.status_code == 201
    audio_url = r.json()["steps"][0]["audio_url"]
    r = await client.post(f"/api/routes/{rid}/steps", headers=vol, files={"audio": ("s.ogg", clip, "audio/ogg")})
    assert r.status_code == 400 and r.json()["error"]["code"] == "step_text_required"
    r = await client.post(f"/api/routes/{rid}/steps", headers=vol, files={"audio": ("x.ogg", b"not audio", "audio/ogg")}, data={"text": "x"})
    assert r.status_code == 415

    assert (await client.get(f"/api/public/routes/{rid}")).status_code == 404  # draft is private
    assert (await client.post(f"/api/routes/{rid}/publish", headers=vol)).status_code == 403  # volunteer cannot self-approve

    r = await client.post(f"/api/routes/{rid}/publish", headers=admin)
    assert r.status_code == 200 and r.json()["status"] == "published"
    pub = (await client.get(f"/api/public/routes/{rid}")).json()
    assert pub["steps"][0]["text"] == "Exit library, turn left."
    assert pub["verified_by"].startswith("Exam Section Head")
    a = await client.get(audio_url.replace("http://localhost:8000", ""))
    assert a.status_code == 200 and a.headers["content-type"] == "audio/ogg"

    qr = await client.get(f"/api/public/routes/{rid}/qr.png")
    assert qr.status_code == 200 and qr.content[:8] == b"\x89PNG\r\n\x1a\n"

    # Editing a live route is blocked until it is unpublished.
    assert (await client.post(f"/api/routes/{rid}/steps", headers=admin, data={"text": "x"})).status_code == 409


# ------------------------------------------------------------------------------ Buzz Codes & push


async def test_buzz_codes_custom_patterns_and_dead_subscriptions(client, admin, fakes, pushes):
    await join_student(client, admin)
    t = await student_token()
    key = (await client.get("/api/public/push-key")).json()["public_key"]
    assert len(key) > 80

    codes = (await client.get("/api/me/buzz-codes", params={"t": t})).json()["codes"]
    assert {c["type"] for c in codes} >= {"notice", "deadline", "clash", "exam", "update", "personal"}

    r = await client.put("/api/me/buzz-codes/exam", params={"t": t}, json={"pattern": [5000]})
    assert r.status_code == 400
    r = await client.put("/api/me/buzz-codes/exam", params={"t": t}, json={"pattern": [100, 100, 100]})
    assert r.status_code == 200

    for ep in ("https://push.example/ok", "https://push.example/gone"):
        await client.post("/api/me/push/subscribe", params={"t": t}, json={"endpoint": ep, "keys": {"p256dh": "x", "auth": "y"}})
    r = await client.post("/api/me/push/test", params={"t": t, "kind": "exam"})
    assert r.json()["sent_to_devices"] == 1
    assert pushes[-1]["vibrate"] == [100, 100, 100]
    # The expired browser subscription was removed, so only one device remains.
    r = await client.post("/api/me/push/test", params={"t": t, "kind": "notice"})
    assert r.json()["sent_to_devices"] == 1

    # Resetting goes back to the default pattern.
    await client.put("/api/me/buzz-codes/exam", params={"t": t}, json={"pattern": None})
    await client.post("/api/me/push/test", params={"t": t, "kind": "exam"})
    assert pushes[-1]["vibrate"] == [450, 150, 450, 150, 450]


async def test_push_key_unavailable_without_vapid(client):
    r = await client.get("/api/public/push-key")
    assert r.status_code == 503
