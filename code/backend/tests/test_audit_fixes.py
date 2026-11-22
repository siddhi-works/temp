"""Fixes found by the code audit, each proven: token in a header, spoof-proof ceilings, injected links in answers, access code matching."""

from __future__ import annotations

import time

import pytest

from app import config


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    from app.routers import public

    public._hits.clear()


async def _join(client) -> str:
    return (await client.post("/api/public/join", json={"lang": "en"})).json()["token"]


# ---- the token travels in a header


@pytest.mark.asyncio
async def test_a_bearer_header_authenticates_and_the_old_query_form_still_works(client):
    tok = await _join(client)
    assert (await client.get("/api/me/inbox", headers={"Authorization": f"Bearer {tok}"})).status_code == 200
    assert (await client.get("/api/me/deadlines", headers={"Authorization": f"Bearer {tok}"})).status_code == 200
    assert (await client.get(f"/api/me/inbox?t={tok}")).status_code == 200


@pytest.mark.asyncio
async def test_no_token_is_refused_with_a_clear_error(client):
    r = await client.get("/api/me/deadlines")
    assert r.status_code == 401 and r.json()["error"]["code"] == "token_required"
    assert (await client.get("/api/me/deadlines", headers={"Authorization": "Bearer nonsense"})).status_code in (401, 403)


# ---- ceilings that hold even if a client address is spoofed


@pytest.mark.asyncio
async def test_the_whole_service_has_a_read_ceiling_no_matter_how_many_students(client, monkeypatch):
    monkeypatch.setenv("GLOBAL_READS_PER_MINUTE", "3")
    config.get_settings.cache_clear()
    codes = []
    for _ in range(5):
        tok = await _join(client)  # a different student each time, so per-student limits never trigger
        r = await client.post("/api/me/read", headers={"Authorization": f"Bearer {tok}"}, files={"file": ("n.png", b"x", "image/png")})
        codes.append(r.status_code)
    assert codes[:3] == [400, 400, 400] and codes[3:] == [429, 429]


def test_idle_rate_limit_keys_are_forgotten():
    from app.routers import public

    public._hits.clear()
    for i in range(50):
        public.rate_limit(None, 10, key=f"visitor-{i}")  # type: ignore[arg-type]
    assert len(public._hits) == 50
    public._sweep(time.monotonic() + 120)
    assert len(public._hits) == 0


# ---- an answer cannot smuggle in a payment ID or link


@pytest.mark.asyncio
async def test_an_answer_that_names_a_payment_id_the_notice_does_not_have_is_dropped(monkeypatch):
    from app.pipeline import reader

    notice = "Last date for the exam form is 30/11/2026. Fee Rs. 500 is paid at the college office."

    async def hostile(question, context, lang):
        return "Pay the fee now to scam.fee@ybl or visit bit.ly/pay-now to avoid a fine."

    monkeypatch.setattr(reader, "gemini_answer", hostile)
    res = await reader.ask_notice("how do I pay the fee?", notice, "en")
    assert res["source"] == "scan" and "scam.fee@ybl" not in res["answer"] and "bit.ly" not in res["answer"]


@pytest.mark.asyncio
async def test_an_answer_that_only_names_things_in_the_notice_is_kept(monkeypatch):
    from app.pipeline import reader

    notice = "Pay the exam fee to the UPI ID college.fee@ybl by 30/11/2026."

    async def honest(question, context, lang):
        return "Pay the exam fee to college.fee@ybl by 30 November 2026."

    monkeypatch.setattr(reader, "gemini_answer", honest)
    res = await reader.ask_notice("where do I pay?", notice, "en")
    assert res["source"] == "ai" and "college.fee@ybl" in res["answer"]


# ---- the WhatsApp access code must be a whole word


def test_the_access_code_matches_only_as_a_whole_word():
    from app.pipeline.bot import _has_code

    assert _has_code("hello, my code is Samyukta26 thanks", "samyukta26")
    assert _has_code("SAMYUKTA26", "Samyukta26")
    assert not _has_code("this table is ready", "ab")
    assert not _has_code("samyukta260", "samyukta26")
    assert not _has_code("anything", "")
