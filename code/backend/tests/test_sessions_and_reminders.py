"""Anonymous-session clean-up, honest reminders, WhatsApp off by default, strict number checks, trustworthy client address."""

from __future__ import annotations

from datetime import date, timedelta
from types import SimpleNamespace

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app import config
from app.db import sessionmaker, utcnow
from app.models import PersonalDeadline, Student
from app.pipeline.housekeeping import purge_inactive_sessions


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    from app.routers import public

    public._hits.clear()


async def _student(phone_hash: str, idle_days: float | None, created_days_ago: float = 10) -> str:
    async with sessionmaker()() as s:
        st = Student(
            phone_hash=phone_hash,
            phone_enc="none",
            created_at=utcnow() - timedelta(days=created_days_ago),
            last_inbound_at=None if idle_days is None else utcnow() - timedelta(days=idle_days),
        )
        s.add(st)
        await s.commit()
        return st.id


async def _deadline(student_id: str, status: str, due: date) -> None:
    async with sessionmaker()() as s:
        s.add(PersonalDeadline(student_id=student_id, label="Lab journal", due_date=due, status=status, needs_date=False))
        await s.commit()


async def _alive(*ids: str) -> set[str]:
    async with sessionmaker()() as s:
        return set((await s.scalars(select(Student.id).where(Student.id.in_(ids)))).all())


# ---------------------------------------------------------------- the 3-day rule


@pytest.mark.asyncio
async def test_idle_anonymous_sessions_are_deleted_after_three_days_and_the_rest_are_kept(dbready):
    idle = await _student("web-idle", idle_days=4)
    never_seen = await _student("web-never", idle_days=None, created_days_ago=5)
    recent = await _student("web-recent", idle_days=1)
    has_deadline = await _student("web-deadline", idle_days=9)
    await _deadline(has_deadline, "confirmed", date.today() + timedelta(days=5))
    only_pending = await _student("web-pending", idle_days=9)
    await _deadline(only_pending, "pending", date.today() + timedelta(days=5))
    with_phone = await _student("a-real-phone-hash", idle_days=30)

    removed = await purge_inactive_sessions(3)

    assert removed == 3
    assert await _alive(idle, never_seen, recent, has_deadline, only_pending, with_phone) == {recent, has_deadline, with_phone}
    async with sessionmaker()() as s:  # the deleted student's unconfirmed deadline went with them
        assert (await s.scalars(select(PersonalDeadline.id).where(PersonalDeadline.student_id == only_pending))).all() == []


@pytest.mark.asyncio
async def test_zero_days_switches_the_clean_up_off(dbready):
    idle = await _student("web-idle", idle_days=40)
    assert await purge_inactive_sessions(0) == 0
    assert await _alive(idle) == {idle}


@pytest.mark.asyncio
async def test_using_the_app_keeps_a_session_alive_with_at_most_one_write_an_hour(client):
    tok = (await client.post("/api/public/join", json={"lang": "en"})).json()["token"]
    from app.security import verify_student_token

    sid = verify_student_token(tok)
    headers = {"Authorization": f"Bearer {tok}"}

    async def set_last_used(delta: timedelta):
        async with sessionmaker()() as s:
            st = await s.get(Student, sid)
            st.last_inbound_at = utcnow() - delta
            await s.commit()

    async def last_used():
        async with sessionmaker()() as s:
            return (await s.get(Student, sid)).last_inbound_at

    await set_last_used(timedelta(days=2))
    assert (await client.get("/api/me/deadlines", headers=headers)).status_code == 200
    assert utcnow() - await last_used() < timedelta(minutes=1)  # refreshed

    await set_last_used(timedelta(minutes=10))
    await client.get("/api/me/deadlines", headers=headers)
    assert utcnow() - await last_used() > timedelta(minutes=9)  # not rewritten on every request


# ---------------------------------------------------------------- honest reminders


@pytest.mark.asyncio
async def test_saving_a_deadline_does_not_promise_a_buzz_the_phone_cannot_give(client):
    tok = (await client.post("/api/public/join", json={"lang": "en"})).json()["token"]
    from app.security import verify_student_token

    sid = verify_student_token(tok)
    await _deadline(sid, "pending", date.today() + timedelta(days=10))
    async with sessionmaker()() as s:
        pid = (await s.scalar(select(PersonalDeadline.id).where(PersonalDeadline.student_id == sid)))
    r = await client.post(f"/api/me/catch/{pid}/confirm", headers={"Authorization": f"Bearer {tok}"}, json={})
    assert r.status_code == 200
    text = r.json()["message"].lower()
    assert "calendar" in text and "vibration alerts" in text and "remind you" not in text


# ---------------------------------------------------------------- numbers in answers


def test_a_single_digit_changed_in_an_answer_is_caught():
    from app.pipeline.reader import is_grounded

    notice = "Seating: 1234 | Room 12 | Bench 4"
    ref = date(2026, 11, 22)
    assert is_grounded("Your bench is 4 in room 12.", notice, ref)
    assert not is_grounded("Your bench is 7 in room 12.", notice, ref)
    assert not is_grounded("Your bench is 4 in room 13.", notice, ref)


# ---------------------------------------------------------------- WhatsApp is off unless switched on


def test_credentials_alone_do_not_turn_whatsapp_on(monkeypatch):
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    config.get_settings.cache_clear()
    assert config.get_settings().whatsapp_ready is False
    monkeypatch.setenv("WHATSAPP_ENABLED", "true")
    config.get_settings.cache_clear()
    assert config.get_settings().whatsapp_ready is True


@pytest.mark.asyncio
async def test_no_whatsapp_webhook_in_a_student_only_deployment(dbready, monkeypatch):
    monkeypatch.setenv("COLLEGE_MODE", "false")
    monkeypatch.setenv("WHATSAPP_ENABLED", "false")
    config.get_settings.cache_clear()
    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        assert (await c.get("/webhooks/whatsapp")).status_code == 404


# ---------------------------------------------------------------- which address a limit counts


@pytest.mark.parametrize("hops, expected", [(-1, "6.6.6.6"), (0, "127.0.0.1"), (1, "10.0.0.1"), (2, "1.2.3.4")])
def test_only_addresses_our_own_proxies_added_are_trusted(monkeypatch, hops, expected):
    from app.routers.public import client_address

    monkeypatch.setenv("TRUSTED_PROXY_HOPS", str(hops))
    config.get_settings.cache_clear()
    req = SimpleNamespace(headers={"x-forwarded-for": "6.6.6.6, 1.2.3.4, 10.0.0.1"}, client=SimpleNamespace(host="127.0.0.1"))
    assert client_address(req) == expected  # 6.6.6.6 is whatever the client claimed
