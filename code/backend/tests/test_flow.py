"""End-to-end lifecycle through the real HTTP API, database, job queue and webhook."""

from sqlalchemy import select

from app.db import sessionmaker
from app.models import Delivery, Job, Notice, Reminder, Student
from app.pipeline.worker import drain

from .conftest import (
    NOTICE_V2_TEXT,
    extraction_for,
    notice_image,
    wa_payload,
    wa_status,
)

PHONE = "919812345678"


async def join_student(client, admin, phone=PHONE, lang=None, msg_id="in-1"):
    r = await client.post("/webhooks/whatsapp", json=wa_payload(phone, msg_id, text="JOIN MODERN"))
    assert r.status_code == 200 and r.json()["messages"] == 1
    await drain()
    if lang:
        await client.post("/webhooks/whatsapp", json=wa_payload(phone, msg_id + "-lang", text=f"LANG {lang.upper()}"))
        await drain()


async def upload(client, admin, label="v1", **form):
    files = {"file": ("notice.jpg", notice_image(label), "image/jpeg")}
    r = await client.post("/api/notices", headers=admin, files=files, data=form)
    assert r.status_code == 202, r.text
    return r.json()


async def test_full_lifecycle_from_scan_to_read_receipt(client, admin, fakes):
    await join_student(client, admin, lang="mr")
    assert "connected to Modern College" in fakes.sent[0][2]

    body = await upload(client, admin, language_hint="mr")
    nid = body["notice"]["id"]
    assert body["duplicate"] is False
    await drain()

    detail = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    assert detail["status"] == "needs_review"
    assert detail["ocr_engine"] == "sarvam-vision"
    acts = {a["label"]: a for a in detail["actions"]}
    # The weekday in the notice is wrong (27 Nov 2026 is a Friday): flagged, not trusted.
    assert acts["Submit exam form and pay fee"]["verification"] == "needs_check"
    assert any("Friday" in r for r in acts["Submit exam form and pay fee"]["verify_reasons"])
    assert acts["Collect hall ticket"]["verification"] == "verified"
    assert detail["confidence"] < 1

    # Approval is blocked until a human confirms the flagged date.
    r = await client.post(f"/api/notices/{nid}/approve", headers=admin, json={})
    assert r.status_code == 409
    assert r.json()["error"]["code"] == "unverified_dates"
    flagged_id = r.json()["error"]["detail"][0]["id"]

    r = await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": [flagged_id], "minutes": 3})
    assert r.status_code == 200
    await drain()

    detail = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    assert detail["status"] == "published"
    assert {r["lang"] for r in detail["renditions"]} >= {"en", "mr"}
    assert all(r["audio_url"] for r in detail["renditions"])
    assert detail["approved_by"] == "Exam Section"

    # The student got a voice note first, then the text with an approved-by line and link.
    kinds = [m[0] for m in fakes.sent]
    assert kinds[-2:] == ["audio", "text"]
    text = fakes.sent[-1][2]
    assert "अधिकृत सूचना" in text and "https://app.example/n/" in text
    assert "तपासून मंजूर" in text

    # Audio is real and served.
    audio_url = detail["renditions"][0]["audio_url"].replace("http://localhost:8000", "")
    a = await client.get(audio_url)
    assert a.status_code == 200 and a.headers["content-type"].startswith("audio/")

    # Read receipt from Meta updates the delivery; out-of-order "delivered" never downgrades it.
    async with sessionmaker()() as s:
        d = await s.scalar(select(Delivery))
        mid = d.wa_message_ids[-1]
    await client.post("/webhooks/whatsapp", json=wa_status(mid, "read"))
    await client.post("/webhooks/whatsapp", json=wa_status(mid, "delivered"))
    async with sessionmaker()() as s:
        assert (await s.scalar(select(Delivery))).status == "read"
        reminders = (await s.scalars(select(Reminder))).all()
    assert reminders, "reminders scheduled for confirmed dates"

    # Public page is accessible HTML with headings, key dates first, and audio.
    page = await client.get(f"/n/{detail['public_id']}")
    assert page.status_code == 200
    html = page.text
    assert html.index("Key dates") < html.index("Full notice")
    assert "<audio controls" in html and 'lang="mr"' in html
    assert "Checked and approved by Exam Section" in html

    ics = await client.get(f"/api/public/notices/{detail['public_id']}/calendar.ics")
    assert "BEGIN:VEVENT" in ics.text and "DTSTART;TZID=Asia/Kolkata:20261202T103000" in ics.text

    impact = (await client.get("/api/impact", headers=admin)).json()
    assert impact["notices"]["published"] == 1
    assert impact["deliveries"]["read"] == 1
    assert impact["dates"]["confirmed_by_humans"] == 1
    csv_text = (await client.get("/api/impact/log.csv", headers=admin)).text
    assert detail["public_id"] in csv_text


async def test_student_commands_and_question_answering(client, admin, fakes):
    await join_student(client, admin)
    await upload(client, admin)
    await drain()
    nid = (await client.get("/api/review-queue", headers=admin)).json()["queue"][0]["id"]
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    await drain()

    for i, cmd in enumerate(["DEADLINES", "मुदत", "When is my exam form due?", "INBOX", "STOP"]):
        await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, f"cmd-{i}", text=cmd))
        await drain()
    replies = [m[2] for m in fakes.sent if m[0] == "text"][-5:]
    assert "Upcoming:" in replies[0] and "Friday, 27 November 2026" in replies[0]
    assert "Upcoming:" in replies[1]
    assert replies[2] == fakes.answer
    assert "https://app.example/me?t=" in replies[3]
    assert "will not get notices" in replies[4]

    # The signed inbox link works and shows upcoming deadlines.
    token = replies[3].split("t=")[1].strip()
    inbox = (await client.get("/api/me/inbox", params={"t": token})).json()
    assert inbox["upcoming"][0]["due_date"] == "2026-11-27"
    assert (await client.get("/api/me/inbox", params={"t": token + "x"})).status_code == 401


async def test_correction_creates_new_version_and_forward_check_flags_old_copy(client, admin, fakes):
    await join_student(client, admin)
    v1 = (await upload(client, admin, label="v1"))["notice"]["id"]
    await drain()
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{v1}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    await client.post(f"/api/notices/{v1}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    await drain()

    # Office issues a revised notice with a new date.
    fakes.current_text = NOTICE_V2_TEXT
    fakes.extraction = extraction_for(NOTICE_V2_TEXT, "2026-11-29", "29/11/2026 (Sunday)")
    v2 = (await upload(client, admin, label="v2", supersedes_id=v1))["notice"]["id"]
    await drain()
    d2 = (await client.get(f"/api/notices/{v2}", headers=admin)).json()
    assert d2["version"] == 2
    assert any("date changed from Friday, 27 November 2026 to Sunday, 29 November 2026" in c for c in d2["changes_from_previous"])
    r = await client.post(f"/api/notices/{v2}/approve", headers=admin, json={"confirmed_action_ids": [a["id"] for a in d2["actions"] if a["verification"] == "needs_check"]})
    assert r.status_code == 200
    await drain()

    v1_detail = (await client.get(f"/api/notices/{v1}", headers=admin)).json()
    assert v1_detail["status"] == "superseded"
    update_msg = fakes.sent[-1][2]
    assert "What changed:" in update_msg

    async with sessionmaker()() as s:
        old_reminders = (await s.scalars(select(Reminder).where(Reminder.notice_id == v1))).all()
        assert all(r.status == "cancelled" for r in old_reminders)

    # A classmate forwards a photo of the OLD notice on WhatsApp.
    fakes.current_text = __import__("tests.conftest", fromlist=["NOTICE_TEXT"]).NOTICE_TEXT
    fakes.media["img-old"] = (notice_image("forwarded-old"), "image/jpeg")
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "fwd-1", image_id="img-old"))
    await drain()
    reply = [m for m in fakes.sent if m[0] == "text"][-1][2]
    assert "OLD version" in reply and "date changed" in reply and "/n/" in reply

    # Same check from the public web page.
    r = await client.post("/api/public/check", files={"file": ("x.jpg", notice_image("web-old"), "image/jpeg")}, data={"college_code": "modern"})
    check_id = r.json()["check_id"]
    await drain()
    res = (await client.get(f"/api/public/check/{check_id}")).json()
    assert res["result"] == "superseded" and res["latest"]["version"] == 2


async def test_unknown_forward_gets_unverified_reading_and_goes_to_review(client, admin, fakes):
    await join_student(client, admin)
    fakes.media["img-x"] = (notice_image("unknown"), "image/jpeg")
    await client.post("/webhooks/whatsapp", json=wa_payload(PHONE, "fwd-x", image_id="img-x"))
    await drain()
    texts = [m[2] for m in fakes.sent if m[0] == "text"]
    assert any("NOT in your college's official list" in t for t in texts)
    queue = (await client.get("/api/review-queue", headers=admin)).json()["queue"]
    assert queue and queue[0]["source"] == "forward"
    # A forwarded copy can never be published to everyone.
    r = await client.post(f"/api/notices/{queue[0]['id']}/approve", headers=admin, json={"confirmed_action_ids": []})
    assert r.status_code == 409


async def test_staff_edit_marks_dates_human_verified_and_regenerates_diff(client, admin, fakes):
    nid = (await upload(client, admin))["notice"]["id"]
    await drain()
    d = (await client.get(f"/api/notices/{nid}", headers=admin)).json()
    actions = [{"id": a["id"], "kind": a["kind"], "label": a["label"], "due_date": a["due_date"], "due_time": a["due_time"], "amount": a["amount"], "venue": a["venue"]} for a in d["actions"]]
    actions[0]["due_date"] = "2026-11-28"
    r = await client.patch(f"/api/notices/{nid}", headers=admin, json={"actions": actions, "title": "Exam form: last date"})
    assert r.status_code == 200, r.text
    edited = r.json()
    assert edited["title"] == "Exam form: last date"
    assert edited["actions"][0]["verification"] == "human_verified"
    assert edited["actions"][1]["verification"] == "verified"  # untouched rows keep their check
    assert (await client.post(f"/api/notices/{nid}/approve", headers=admin, json={})).status_code == 200


async def test_duplicate_upload_is_deduplicated(client, admin, fakes):
    a = await upload(client, admin, label="same")
    b = await upload(client, admin, label="same")
    assert b["duplicate"] is True and b["notice"]["id"] == a["notice"]["id"]
    async with sessionmaker()() as s:
        assert len((await s.scalars(select(Notice))).all()) == 1
        assert len((await s.scalars(select(Job).where(Job.type == "process_notice"))).all()) == 1


async def test_audience_tags_target_only_matching_students(client, admin, fakes):
    r = await client.post("/api/students", headers=admin, json={"phone": "9800000001", "audience_tags": ["FY-BCOM"], "consent_confirmed": True})
    assert r.status_code == 201
    await client.post("/api/students", headers=admin, json={"phone": "9800000002", "audience_tags": ["SY-BSC"], "consent_confirmed": True})
    nid = (await upload(client, admin, audience_tags="fy-bcom"))["notice"]["id"]
    await drain()
    flagged = [a["id"] for a in (await client.get(f"/api/notices/{nid}", headers=admin)).json()["actions"] if a["verification"] == "needs_check"]
    await client.post(f"/api/notices/{nid}/approve", headers=admin, json={"confirmed_action_ids": flagged})
    await drain()
    async with sessionmaker()() as s:
        deliveries = (await s.scalars(select(Delivery))).all()
        students = {st.id: st for st in (await s.scalars(select(Student))).all()}
    assert len(deliveries) == 1 and students[deliveries[0].student_id].audience_tags == ["FY-BCOM"]
    # Enrolled but never messaged: held until the 24-hour window opens.
    assert deliveries[0].status == "deferred_window"
