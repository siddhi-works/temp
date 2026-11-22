"""Student mode on WhatsApp: the same verified reading as the website, Ask your notice, and safe account linking.

Real: HTTP API, database, job queue, webhook, verification code, sealing, ffmpeg voice-note conversion.
Fake (as everywhere else): Sarvam, Gemini, WhatsApp Cloud API. Dates are relative to today so these tests do not expire.
"""

from __future__ import annotations

import base64
import itertools
import re
from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from sqlalchemy import func, select

from app import config
from app.db import sessionmaker
from app.models import AudioCache, InboundMessage, Notice, OcrCache, PersonalDeadline, RecentRead, Reminder, Student
from app.pipeline.worker import drain

from .conftest import _silent_mp3, extraction_for, notice_image, wa_payload

ME = "919812345678"
OTHER = "919800000001"
_ids = itertools.count(1)


def make_notice(due: date, hall: date) -> tuple[str, dict]:
    text = (
        "EXAMINATION SECTION\nNOTICE\n"
        f"Last date for exam form submission: {due:%d/%m/%Y} ({due:%A}).\n"
        "Exam fee Rs. 500/- to be paid at the office.\n"
        f"Hall tickets will be distributed on {hall:%d.%m.%Y} at 10.30 am in Room 14.\n"
    )
    ex = extraction_for(text, due.isoformat(), f"{due:%d/%m/%Y} ({due:%A})")
    ex["actions"][1].update(due_date=hall.isoformat(), source_quote=f"Hall tickets will be distributed on {hall:%d.%m.%Y} at 10.30 am in Room 14.")
    ex["summaries"] = {k: "Submit your winter exam form and pay 500 rupees at the college office." for k in ("en", "mr", "hi")}
    return text, ex


@pytest.fixture
def engine(monkeypatch, fakes, tmp_path):
    """The reading engine with its AI and speech providers faked. `st` lets a test change what they return."""
    from app.pipeline import bot, reader
    from app.providers.llm import NoticeExtraction
    from app.providers.ocr import OcrResult
    from app.routers import me, public

    public._hits.clear()
    due, hall = date.today() + timedelta(days=10), date.today() + timedelta(days=14)
    text, ex = make_notice(due, hall)
    st = SimpleNamespace(
        due=due, hall=hall, text=text, extraction=ex, cache=None, asked=None, answer_error=None,
        answer=f"Your exam form is due on {due:%A}, {due.day} {due:%B} {due.year}.",
    )

    async def fake_ocr(doc, lang="mr", prefer=None, cache=True):
        st.cache = cache
        return OcrResult(text=st.text, engine="fake", pages=1)

    async def fake_extract(ocr_text, **kw):
        return NoticeExtraction.model_validate(st.extraction)

    async def fake_speak(text, lang):
        return _silent_mp3()

    async def fake_answer(question, context, lang):
        st.asked = (question, context)
        if st.answer_error:
            raise st.answer_error
        return st.answer

    monkeypatch.setattr(reader, "run_ocr", fake_ocr)
    monkeypatch.setattr(reader, "gemini_extract", fake_extract)
    monkeypatch.setattr(reader, "speak_uncached", fake_speak)
    monkeypatch.setattr(reader, "gemini_answer", fake_answer)
    monkeypatch.setattr(bot, "speak_uncached", fake_speak)
    monkeypatch.setattr(me, "speak_uncached", fake_speak)
    fakes.media["img-1"] = (notice_image(), "image/jpeg")
    return st


async def say(client, phone: str, text: str | None = None, image: str | None = None, audio: str | None = None) -> None:
    r = await client.post("/webhooks/whatsapp", json=wa_payload(phone, f"m-{next(_ids)}", text=text, image_id=image, audio_id=audio))
    assert r.status_code == 200, r.text
    await drain()


def texts(fakes, phone: str = ME) -> list[str]:
    return [m[2] for m in fakes.sent if m[0] == "text" and m[1] == phone]


def audios(fakes, phone: str = ME) -> list:
    return [m for m in fakes.sent if m[0] == "audio" and m[1] == phone]


async def count(model) -> int:
    async with sessionmaker()() as s:
        return int(await s.scalar(select(func.count()).select_from(model)) or 0)


async def web_student(client) -> str:
    return (await client.post("/api/public/join", json={"lang": "en"})).json()["token"]


# ------------------------------------------------------------------ one engine, two channels


async def test_forwarding_a_notice_on_whatsapp_gives_the_verified_reading_and_a_voice_note(client, fakes, engine, tmp_path):
    await say(client, ME, image="img-1")
    msgs = texts(fakes)
    assert "Welcome to Samyukta" in msgs[0]  # privacy notice before the first answer
    assert "Reading this notice now" in msgs[1]
    reading = msgs[2]
    assert "Dates I checked against the notice:" in reading
    from app.pipeline.dates import speak_date_lang

    assert f"1. Submit exam form and pay fee: {speak_date_lang(engine.due, 'en')}" in reading  # the date is checked and spoken
    assert "not confirmed" not in reading
    assert len(audios(fakes)) == 1  # the voice note
    assert engine.cache is False  # the OCR cache was bypassed


async def test_nothing_about_the_notice_is_stored_except_a_sealed_copy_for_one_hour(client, fakes, engine, tmp_path):
    await say(client, ME, image="img-1")
    assert await count(Notice) == 0 and await count(OcrCache) == 0 and await count(AudioCache) == 0
    assert not [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]  # no scan, no audio on disk
    async with sessionmaker()() as s:
        row = await s.scalar(select(RecentRead))
        assert row is not None and "Hall tickets" not in row.sealed and "Room 14" not in row.sealed  # encrypted, not plain text
        assert row.expires_at - row.created_at <= timedelta(hours=1, seconds=5)
        # The only plain trace of the exchange is the message record the webhook has always kept: type, and caption if any.
        msg = (await s.scalars(select(InboundMessage))).one()
        assert msg.text is None and msg.media_id == "img-1"


async def test_a_date_that_is_not_in_the_scan_is_flagged_and_cannot_be_saved(client, fakes, engine):
    engine.extraction["actions"][0]["due_date"] = (engine.due + timedelta(days=3)).isoformat()  # the AI "invents" a date
    await say(client, ME, image="img-1")
    reading = texts(fakes)[2]
    assert "1. Submit exam form and pay fee: date not confirmed" in reading
    assert "Please confirm" in reading or "confirm" in reading.lower()
    await say(client, ME, text="SAVE 1")
    assert "will not save that date" in texts(fakes)[-1]
    assert await count(PersonalDeadline) == 0


async def test_an_unreadable_file_gets_a_plain_message_not_a_crash(client, fakes, engine):
    fakes.media["bad"] = (b"MZ" + b"\x00" * 400, "application/pdf")
    await say(client, ME, image="bad")
    assert "Reading this notice now" in "".join(texts(fakes))
    assert any("PDF or photo" in m or "could not" in m for m in texts(fakes))
    assert not audios(fakes)


# ------------------------------------------------------------------ Ask your notice


async def test_a_question_is_answered_from_that_notice_with_the_source_line(client, fakes, engine):
    await say(client, ME, image="img-1")
    await say(client, ME, text="When is the exam form deadline?")
    answer = texts(fakes)[-1]
    assert f"{engine.due.day}" in answer and "From the notice:" in answer and "Last date for exam form submission" in answer
    assert "check it on the notice" not in answer  # grounded: nothing to warn about
    assert "Hall tickets" in engine.asked[1]  # the model was given this notice, not a general knowledge prompt


async def test_an_answer_with_a_date_that_is_not_in_the_notice_is_marked_unverified(client, fakes, engine):
    engine.answer = f"The form is due on {(engine.due + timedelta(days=5)):%A}, {(engine.due + timedelta(days=5)).day} {(engine.due + timedelta(days=5)):%B}."
    await say(client, ME, image="img-1")
    await say(client, ME, text="When is the deadline?")
    assert "could not match every date or number" in texts(fakes)[-1]


async def test_an_answer_with_a_wrong_weekday_is_marked_unverified(client, fakes, engine):
    wrong = engine.due + timedelta(days=1)  # right date number, wrong weekday
    engine.answer = f"It is due on {engine.due:%d/%m/%Y} ({wrong:%A})."
    await say(client, ME, image="img-1")
    await say(client, ME, text="deadline?")
    assert "could not match every date or number" in texts(fakes)[-1]


async def test_when_the_ai_is_down_the_closest_lines_of_the_notice_are_quoted(client, fakes, engine):
    from app.providers.base import ProviderError

    engine.answer_error = ProviderError("gemini", "quota", retryable=False)
    await say(client, ME, image="img-1")
    await say(client, ME, text="where do I collect my hall ticket")
    answer = texts(fakes)[-1]
    assert "closest lines in the notice" in answer and "Room 14" in answer


async def test_a_voice_question_is_answered_with_text_and_a_voice_note_and_nothing_is_cached(client, fakes, engine, monkeypatch, tmp_path):
    from app.pipeline import bot

    async def stt(data, mime):
        return "when is the form deadline"

    monkeypatch.setattr(bot, "transcribe", stt)
    fakes.media["voice-1"] = (b"OggS" + b"\x00" * 50, "audio/ogg")
    await say(client, ME, image="img-1")
    before = len(audios(fakes))
    await say(client, ME, audio="voice-1")
    assert 'You asked: "when is the form deadline"' in texts(fakes)[-1]
    assert len(audios(fakes)) == before + 1
    assert await count(AudioCache) == 0 and not [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]


async def test_asking_before_sending_a_notice_says_what_to_do(client, fakes, engine):
    await say(client, ME, text="hello")  # greeting: just the welcome
    await say(client, ME, text="when is my exam")
    assert "Send me a photo or PDF of a notice first" in texts(fakes)[-1]


# ------------------------------------------------------------------ Smart Deadline Guardian


async def test_save_needs_the_students_yes_and_then_reminders_exist(client, fakes, engine):
    await say(client, ME, image="img-1")
    await say(client, ME, text="SAVE 1")
    assert "Reply YES to save" in texts(fakes)[-1]
    assert await count(Reminder) == 0  # nothing is scheduled until the student agrees
    await say(client, ME, text="YES")
    assert texts(fakes)[-1].startswith("Saved: ")
    async with sessionmaker()() as s:
        pd = await s.scalar(select(PersonalDeadline))
        assert pd.status == "confirmed" and pd.due_date == engine.due
    assert await count(Reminder) >= 1
    await say(client, ME, text="/deadlines")
    assert "Your saved deadlines" in texts(fakes)[-1] and "Submit exam form and pay fee" in texts(fakes)[-1]


async def test_no_cancels_the_save(client, fakes, engine):
    await say(client, ME, image="img-1")
    await say(client, ME, text="save 2")
    await say(client, ME, text="NO")
    assert "Cancelled" in texts(fakes)[-1]
    assert await count(Reminder) == 0


# ------------------------------------------------------------------ commands, privacy, access code


async def test_slash_commands_work_and_history_is_honest(client, fakes, engine):
    await say(client, ME, text="/start")
    await say(client, ME, text="/help")
    assert "Commands:" in texts(fakes)[-1]
    await say(client, ME, text="/history")
    assert "I do not keep a history" in texts(fakes)[-1] and "keeping no notice" in texts(fakes)[-1]
    await say(client, ME, image="img-1")
    await say(client, ME, text="/history")
    assert "for one hour" in texts(fakes)[-1] and "Hall tickets" not in texts(fakes)[-1]  # the title, never the notice text


async def test_language_can_be_changed_and_the_next_answer_follows_it(client, fakes, engine):
    await say(client, ME, text="/language")
    assert "ENGLISH" in texts(fakes)[-1] and "HINDI" in texts(fakes)[-1]
    await say(client, ME, text="hindi")
    await say(client, ME, image="img-1")
    assert "सूचना से मिलाई गई तारीखें" in "".join(texts(fakes))


async def test_delete_needs_two_steps_and_erases_everything(client, fakes, engine):
    await say(client, ME, image="img-1")
    await say(client, ME, text="SAVE 1")
    await say(client, ME, text="YES")
    await say(client, ME, text="delete")
    assert "DELETE EVERYTHING" in texts(fakes)[-1]
    assert await count(Student) == 1  # not yet
    await say(client, ME, text="delete everything")
    assert "Everything is erased" in texts(fakes)[-1]
    assert await count(Student) == 0 and await count(PersonalDeadline) == 0 and await count(Reminder) == 0
    assert await count(RecentRead) == 0 and await count(InboundMessage) == 0


async def test_the_access_code_gates_a_new_whatsapp_student(client, fakes, engine, monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "TEAM123")
    config.get_settings.cache_clear()
    await say(client, ME, image="img-1")
    assert "send the access code" in texts(fakes)[-1] and not audios(fakes)  # no paid work for a stranger
    await say(client, ME, text="team123")
    assert "Welcome to Samyukta" in texts(fakes)[-1]
    await say(client, ME, image="img-1")
    assert audios(fakes)


async def test_the_daily_limit_stops_a_flood(client, fakes, engine, monkeypatch):
    monkeypatch.setenv("STUDENT_DAILY_FORWARD_LIMIT", "1")
    config.get_settings.cache_clear()
    await say(client, ME, image="img-1")
    await say(client, ME, image="img-1")
    await say(client, ME, image="img-1")
    assert "sent many notices today" in texts(fakes)[-1]
    assert len(audios(fakes)) == 1  # one notice is allowed; the second and third are refused before any paid work


# ------------------------------------------------------------------ linking the website and WhatsApp


@pytest.fixture
def linkable(monkeypatch):
    monkeypatch.setenv("WHATSAPP_BUSINESS_NUMBER", "919700000000")
    config.get_settings.cache_clear()


async def start(client, token) -> dict:
    r = await client.post(f"/api/me/whatsapp/link?t={token}")
    assert r.status_code == 200, r.text
    return r.json()


def confirm_code_from(fakes, phone: str) -> str:
    m = re.search(r"website: ([\d ]{7})\.", texts(fakes, phone)[-1])
    assert m, texts(fakes, phone)[-1]
    return m.group(1).replace(" ", "")


async def test_the_qr_holds_only_the_public_number_and_a_random_code(client, fakes, engine, linkable):
    tok = await web_student(client)
    out = await start(client, tok)
    assert out["url"].startswith("https://wa.me/919700000000?text=") and out["code"] in out["url"]
    assert re.fullmatch(r"[A-HJ-NP-Z2-9]{8}", out["code"])
    assert out["qr_svg"].lstrip().startswith(("<?xml", "<svg")) and out["expires_in_seconds"] == 600
    assert "student" not in out["url"].lower() and tok not in out["url"]  # no token, no personal data in a public URL
    st = (await client.get(f"/api/me/whatsapp?t={tok}")).json()
    assert st["available"] and st["waiting"] and not st["linked"] and st["open_url"].startswith("https://wa.me/919700000000")


async def test_linking_needs_the_code_from_whatsapp_typed_on_the_website(client, fakes, engine, linkable):
    tok = await web_student(client)
    # A web student saves a deadline first (Scenario C: it must be there after linking).
    async with sessionmaker()() as s:
        web = (await s.scalars(select(Student))).one()
        s.add(PersonalDeadline(student_id=web.id, label="Physics assignment", due_date=date.today() + timedelta(days=9), status="confirmed", source="voice"))
        await s.commit()
    out = await start(client, tok)
    await say(client, ME, text=f"Hi Samyukta, my link code is {out['code']}")
    confirm = confirm_code_from(fakes, ME)
    # The phone alone has linked nothing yet.
    st = (await client.get(f"/api/me/whatsapp?t={tok}")).json()
    assert not st["linked"] and st["claimed_by_last4"] == ME[-4:]
    bad = await client.post(f"/api/me/whatsapp/confirm?t={tok}", json={"code": "0000" if confirm != "0000" else "1111"})
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "bad_confirm_code"
    ok = await client.post(f"/api/me/whatsapp/confirm?t={tok}", json={"code": confirm})
    assert ok.status_code == 200 and ok.json()["linked"] is True
    assert (await client.get(f"/api/me/whatsapp?t={tok}")).json()["linked"] is True
    # One account, holding the phone and the deadline; the website token still works.
    async with sessionmaker()() as s:
        students = (await s.scalars(select(Student))).all()
        assert len(students) == 1 and not students[0].phone_hash.startswith("web-")
    await say(client, ME, text="deadlines")
    assert "Physics assignment" in texts(fakes)[-1]  # the website's deadline is visible on WhatsApp


async def test_a_reminder_for_a_linked_student_goes_to_whatsapp(client, fakes, engine, linkable):
    from app.pipeline.messaging import notify_student

    tok = await web_student(client)
    out = await start(client, tok)
    await say(client, ME, text=f"link {out['code']}")
    assert (await client.post(f"/api/me/whatsapp/confirm?t={tok}", json={"code": confirm_code_from(fakes, ME)})).status_code == 200
    async with sessionmaker()() as s:
        student = (await s.scalars(select(Student))).one()
        res = await notify_student(s, student, kind="reminder", title="Reminder", text="Tomorrow: Physics assignment")
    assert res["whatsapp"] is True and "Tomorrow: Physics assignment" in texts(fakes)[-1]


async def test_someone_who_photographs_the_qr_cannot_link_their_number(client, fakes, engine, linkable):
    victim_tok = await web_student(client)
    out = await start(client, victim_tok)
    await say(client, OTHER, text=f"Hi Samyukta, my link code is {out['code']}")  # the attacker sends the code first
    attacker_confirm = confirm_code_from(fakes, OTHER)
    # The attacker has a confirm code, but only on their own phone. The victim's website session never gets linked by it...
    st = (await client.get(f"/api/me/whatsapp?t={victim_tok}")).json()
    assert not st["linked"] and st["claimed_by_last4"] == OTHER[-4:]  # ...and the victim can see the number is not theirs
    # Another web session cannot use it either.
    other_tok = await web_student(client)
    r = await client.post(f"/api/me/whatsapp/confirm?t={other_tok}", json={"code": attacker_confirm})
    assert r.status_code == 404 and r.json()["error"]["code"] == "nothing_to_confirm"
    # A second phone cannot take over the same code.
    await say(client, ME, text=f"link {out['code']}")
    assert "already been used from another number" in texts(fakes, ME)[-1]
    async with sessionmaker()() as s:
        assert all(st.phone_hash.startswith("web-") or st.phone_hash for st in (await s.scalars(select(Student))).all())
        web = await s.scalar(select(Student).where(Student.phone_hash.startswith("web-")))
        assert web is not None  # the victim's account still has no phone attached


async def test_five_wrong_codes_cancel_the_link(client, fakes, engine, linkable):
    tok = await web_student(client)
    out = await start(client, tok)
    await say(client, ME, text=f"link {out['code']}")
    real = confirm_code_from(fakes, ME)
    wrong = "0000" if real != "0000" else "1111"
    for _ in range(5):
        assert (await client.post(f"/api/me/whatsapp/confirm?t={tok}", json={"code": wrong})).status_code == 400
    late = await client.post(f"/api/me/whatsapp/confirm?t={tok}", json={"code": real})  # even the right code is refused now
    assert late.status_code == 429 and late.json()["error"]["code"] == "too_many_tries"
    assert not (await client.get(f"/api/me/whatsapp?t={tok}")).json()["linked"]


async def test_an_expired_or_used_code_is_refused(client, fakes, engine, linkable):
    tok = await web_student(client)
    out = await start(client, tok)
    from app.models import LinkCode

    async with sessionmaker()() as s:
        row = await s.scalar(select(LinkCode))
        row.expires_at = row.expires_at - timedelta(minutes=11)
        await s.commit()
    await say(client, ME, text=f"link {out['code']}")
    assert "not valid, or it has expired" in texts(fakes)[-1]
    await say(client, ME, text="link ZZZZZZZZ")  # a code that never existed
    assert "not valid, or it has expired" in texts(fakes)[-1]
    # A completed link cannot be replayed from another number.
    out2 = await start(client, tok)
    await say(client, ME, text=f"link {out2['code']}")
    assert (await client.post(f"/api/me/whatsapp/confirm?t={tok}", json={"code": confirm_code_from(fakes, ME)})).status_code == 200
    await say(client, OTHER, text=f"link {out2['code']}")
    assert "not valid, or it has expired" in texts(fakes, OTHER)[-1]


async def test_a_number_cannot_be_linked_twice_until_it_is_disconnected(client, fakes, engine, linkable):
    tok1 = await web_student(client)
    out1 = await start(client, tok1)
    await say(client, ME, text=f"link {out1['code']}")
    assert (await client.post(f"/api/me/whatsapp/confirm?t={tok1}", json={"code": confirm_code_from(fakes, ME)})).status_code == 200
    tok2 = await web_student(client)
    out2 = await start(client, tok2)
    await say(client, ME, text=f"link {out2['code']}")
    assert "already connected to a Samyukta account" in texts(fakes)[-1]
    await say(client, ME, text="/disconnect")
    assert texts(fakes)[-1].startswith("Disconnected")
    await say(client, ME, text=f"link {out2['code']}")
    assert (await client.post(f"/api/me/whatsapp/confirm?t={tok2}", json={"code": confirm_code_from(fakes, ME)})).status_code == 200


async def test_disconnect_from_the_website_forgets_the_number_and_keeps_the_deadlines(client, fakes, engine, linkable):
    tok = await web_student(client)
    out = await start(client, tok)
    await say(client, ME, text=f"link {out['code']}")
    await client.post(f"/api/me/whatsapp/confirm?t={tok}", json={"code": confirm_code_from(fakes, ME)})
    async with sessionmaker()() as s:
        web = (await s.scalars(select(Student))).one()
        s.add(PersonalDeadline(student_id=web.id, label="Keep me", due_date=date.today() + timedelta(days=5), status="confirmed", source="voice"))
        await s.commit()
    assert (await client.delete(f"/api/me/whatsapp?t={tok}")).json() == {"linked": False}
    async with sessionmaker()() as s:
        web = (await s.scalars(select(Student))).one()
        assert web.phone_hash.startswith("web-") and web.phone_enc == "none"
        assert await s.scalar(select(func.count()).select_from(PersonalDeadline)) == 1
    assert (await client.get(f"/api/me/whatsapp?t={tok}")).json()["linked"] is False


async def test_the_whatsapp_card_is_hidden_when_no_number_is_configured(client, fakes, engine):
    tok = await web_student(client)
    st = (await client.get(f"/api/me/whatsapp?t={tok}")).json()
    assert st["available"] is False and st["open_url"] is None
    r = await client.post(f"/api/me/whatsapp/link?t={tok}")
    assert r.status_code == 503 and r.json()["error"]["code"] == "whatsapp_unavailable"


# ------------------------------------------------------------------ the website: same engine, same answers


async def test_the_website_read_returns_a_token_to_ask_questions_and_stores_nothing(client, fakes, engine, tmp_path):
    tok = await web_student(client)
    r = await client.post(f"/api/me/read?t={tok}", files={"file": ("n.jpg", notice_image(), "image/jpeg")}, data={"lang": "en"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert "_ocr_text" not in body and body["ask_token"] and "Hall tickets" not in body["ask_token"]
    assert body["actions"][0]["status"] == "verified"
    q = await client.post(f"/api/me/ask?t={tok}", data={"ask_token": body["ask_token"], "question": "When is the deadline?", "lang": "en"})
    assert q.status_code == 200, q.text
    ans = q.json()
    assert ans["verified"] is True and f"{engine.due.day}" in ans["answer"] and ans["evidence"] and base64.b64decode(ans["audio_b64"])
    assert await count(RecentRead) == 0 and await count(OcrCache) == 0  # the server kept nothing
    assert not [p for p in (tmp_path / "storage").rglob("*") if p.is_file()]


async def test_a_forged_or_old_ask_token_is_refused(client, fakes, engine, monkeypatch):
    tok = await web_student(client)
    forged = await client.post(f"/api/me/ask?t={tok}", data={"ask_token": "not-a-token", "question": "when?"})
    assert forged.status_code == 410 and forged.json()["error"]["code"] == "ask_expired"
    from app.security import seal, unseal

    token = seal("Last date 27/11/2026")
    assert unseal(token) == "Last date 27/11/2026" and unseal(token, max_age_seconds=3600) is not None
    import cryptography.fernet as fernet

    real = fernet.time.time
    monkeypatch.setattr(fernet.time, "time", lambda: real() + 3601)
    assert unseal(token, 3600) is None  # an hour later it no longer opens
    monkeypatch.undo()
    # A token sealed with another server's secret does not open here.
    monkeypatch.setenv("SECRET_KEY", "another-server")
    config.get_settings.cache_clear()
    assert unseal(token) is None


# ------------------------------------------------------------------ the grounding check on its own


def test_grounding_check_rules():
    from app.pipeline.reader import is_grounded

    scan = "Last date for exam form submission: 27/11/2026 (Friday). Fee Rs. 500/-. परीक्षा २७ नोव्हेंबर २०२६. Hall ticket 02.12.2026 at 10.30 am in Room 14."
    ref = date(2026, 11, 21)
    assert is_grounded("Pay 500 rupees by Friday, 27 November 2026.", scan, ref)
    assert is_grounded("Collect it on 2 December 2026 at 10.30 in Room 14.", scan, ref)
    assert not is_grounded("It is on 28 November 2026.", scan, ref)  # a date that is not in the notice
    assert not is_grounded("Pay 700 rupees.", scan, ref)  # a number that is not in the notice
    assert not is_grounded("Due on Thursday, 27 November 2026.", scan, ref)  # right date, wrong weekday
    assert is_grounded("You can submit it at the office.", scan, ref)  # no claims to check


def test_closest_lines_prefer_the_lines_that_share_the_most_words():
    from app.pipeline.reader import best_lines

    scan = "Notice\n<tr><td>Physics</td><td>Room 12</td></tr>\nHall tickets will be given in Room 14.\nSigned, Principal"
    assert best_lines(scan, "where is the hall ticket")[0].startswith("Hall tickets will be given")
    assert best_lines(scan, "") == [] and best_lines(scan, "zzzz qqqq") == []


def test_the_link_message_is_recognised_in_every_form_it_can_arrive_in_and_not_in_ordinary_sentences():
    from app.pipeline.bot import classify

    assert classify("Hi Samyukta, my link code is K7M2QX4P") == ("link", "K7M2QX4P")  # the QR's pre-filled text
    assert classify("hi samyukta, my link code is k7m2qx4p") == ("link", "K7M2QX4P")  # a phone may change the case
    assert classify("link K7M2QX4P") == ("link", "K7M2QX4P") and classify("/link K7M2QX4P") == ("link", "K7M2QX4P")
    assert classify("link code: K7M2QX4P.") == ("link", "K7M2QX4P")  # a trailing full stop is fine
    assert classify("I need the link for the research paper")[0] == "question"  # words after "link" are not a code
    assert classify("link research paper")[0] == "question"


# ------------------------------------------------------------------ Notice change detector


def _a(label, iso, status="verified", time=None):
    return {"label": label, "due_date": iso, "due_time": time, "status": status}


def test_a_change_is_claimed_only_when_both_notices_were_verified_and_are_about_the_same_thing():
    from app.pipeline.reader import diff_actions

    old = [_a("Submit exam form and pay fee", "2026-12-15"), _a("Collect hall ticket", "2026-12-20", time="10:30")]
    assert diff_actions(old, [_a("Submit exam form and pay fee", "2026-12-18"), _a("Collect hall ticket", "2026-12-20", time="10:30")]) == [
        {"label": "Submit exam form and pay fee", "old": ("2026-12-15", None), "new": ("2026-12-18", None)}
    ]
    assert diff_actions(old, [_a("Collect hall ticket", "2026-12-20", time="14:00")])[0]["new"] == ("2026-12-20", "14:00")  # a time change counts
    assert diff_actions(old, old) == []  # nothing changed
    assert diff_actions(old, [_a("Submit exam form and pay fee", "2026-12-18", status="needs_check")]) == []  # the new date is unverified: no claim
    assert diff_actions([_a("Submit exam form and pay fee", "2026-12-15", status="needs_check")], [_a("Submit exam form and pay fee", "2026-12-18")]) == []
    assert diff_actions(old, [_a("Pay library fine", "2026-12-18")]) == []  # a different subject is not a change


async def test_a_corrected_notice_sent_within_the_hour_says_what_changed_before_reading_it(client, fakes, engine):
    from app.pipeline.dates import speak_date_lang

    first_due = engine.due
    await say(client, ME, image="img-1")
    new_due = first_due + timedelta(days=3)
    engine.text, engine.extraction = make_notice(new_due, engine.hall)
    await say(client, ME, image="img-1")
    msgs = texts(fakes)
    i = next(n for n, m in enumerate(msgs) if "update to the notice you sent earlier" in m)
    change = msgs[i]
    assert f"it was {speak_date_lang(first_due, 'en')}, now {speak_date_lang(new_due, 'en')}" in change
    assert "Collect hall ticket" not in change  # the hall ticket date did not change
    assert "Dates I checked against the notice" in msgs[i + 1]  # the reading follows the warning
    assert "confirm with the college office which one is current" in change


async def test_sending_the_same_notice_again_claims_no_change(client, fakes, engine):
    await say(client, ME, image="img-1")
    await say(client, ME, image="img-1")
    assert not [m for m in texts(fakes) if "update to the notice" in m]


async def test_a_payment_red_flag_is_the_first_thing_spoken_and_written_on_whatsapp(client, fakes, engine):
    engine.text += "Pay the exam fee to the UPI ID asha.college@ybl.\nRegister at examfee.xyz/pay\n"
    await say(client, ME, image="img-1")
    reading = texts(fakes)[2]
    assert reading.splitlines()[2].startswith("Safety check. This notice asks you to pay to the UPI ID asha dot college at ybl")
    assert "Safety check:\n- This notice has a link to examfee dot xyz" in reading  # the quiet finding is listed, not spoken
