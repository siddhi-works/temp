"""Security guardrails, each proven by a test: headers, tokens, authorisation, upload limits, rate limits, secrets."""

from __future__ import annotations

import pytest

from app import config
from app.db import sessionmaker
from app.models import PersonalDeadline


@pytest.fixture(autouse=True)
def fresh_rate_limits():
    from app.routers import public

    public._hits.clear()  # the per-client limiter is process-wide; each test starts clean


async def _join(client) -> str:
    r = await client.post("/api/public/join", json={"lang": "en"})
    assert r.status_code == 201, r.text
    return r.json()["token"]


# ---------------------------------------------------------------- headers and caching


@pytest.mark.asyncio
async def test_every_response_carries_hardening_headers(client):
    r = await client.get("/health")
    assert r.headers["x-content-type-options"] == "nosniff"
    assert r.headers["x-frame-options"] == "DENY"
    assert r.headers["referrer-policy"] == "no-referrer"
    assert "microphone=(self)" in r.headers["permissions-policy"]


@pytest.mark.asyncio
async def test_personal_responses_are_never_cached(client):
    r = await client.post("/api/public/join", json={"lang": "en"})
    assert r.headers["cache-control"] == "no-store"
    tok = r.json()["token"]
    inbox = await client.get(f"/api/me/inbox?t={tok}")
    assert inbox.headers["cache-control"] == "no-store"


# ---------------------------------------------------------------- tokens and authorisation


@pytest.mark.asyncio
async def test_a_tampered_token_is_rejected(client):
    tok = await _join(client)
    forged = tok[:-3] + ("AAA" if not tok.endswith("AAA") else "BBB")
    assert (await client.get(f"/api/me/inbox?t={forged}")).status_code in (401, 403)
    assert (await client.get("/api/me/inbox?t=not-a-token")).status_code in (401, 403)


@pytest.mark.asyncio
async def test_a_student_cannot_confirm_another_students_deadline(client):
    mine, other = await _join(client), await _join(client)
    from app.security import verify_student_token

    async with sessionmaker()() as s:
        pd = PersonalDeadline(student_id=verify_student_token(mine), label="Lab journal", status="pending", needs_date=True)
        s.add(pd)
        await s.commit()
        pid = pd.id
    r = await client.post(f"/api/me/catch/{pid}/confirm?t={other}", json={})
    assert r.status_code == 404  # same answer as "does not exist": no hint that it belongs to someone else


# ---------------------------------------------------------------- locks on a public deployment


@pytest.mark.asyncio
async def test_first_time_setup_needs_the_setup_token_when_one_is_set(client, monkeypatch):
    monkeypatch.setenv("SETUP_TOKEN", "s3cret-setup")
    config.get_settings.cache_clear()
    body = {"college_code": "MODERN", "college_name": "Modern College, Pune", "admin_name": "Exam Head"}
    assert (await client.post("/api/setup/bootstrap", json=body)).status_code == 403
    assert (await client.post("/api/setup/bootstrap", json={**body, "setup_token": "wrong"})).status_code == 403
    assert (await client.post("/api/setup/bootstrap", json={**body, "setup_token": "s3cret-setup"})).status_code == 201


@pytest.mark.asyncio
async def test_joining_needs_the_access_code_when_one_is_set(client, monkeypatch):
    monkeypatch.setenv("DEMO_ACCESS_CODE", "Saathi26")
    config.get_settings.cache_clear()
    assert (await client.post("/api/public/join", json={"lang": "en"})).status_code == 403
    assert (await client.post("/api/public/join", json={"lang": "en", "code": "nope"})).status_code == 403
    assert (await client.post("/api/public/join", json={"lang": "en", "code": " saathi26 "})).status_code == 201


def test_production_refuses_to_start_without_real_secrets():
    s = config.Settings(env="prod", secret_key="dev-only-change-me", phone_encryption_key=None)
    assert {"SECRET_KEY", "PHONE_ENCRYPTION_KEY"} <= set(s.missing_for_prod())


# ---------------------------------------------------------------- abuse limits


@pytest.mark.asyncio
async def test_oversized_and_empty_uploads_are_refused_before_any_processing(client, monkeypatch):
    monkeypatch.setenv("MAX_UPLOAD_MB", "1")
    config.get_settings.cache_clear()
    tok = await _join(client)
    big = await client.post(f"/api/me/read?t={tok}", files={"file": ("n.png", b"0" * (2 * 1024 * 1024), "image/png")})
    assert big.status_code == 413 and big.json()["error"]["code"] == "file_too_large"
    tiny = await client.post(f"/api/me/read?t={tok}", files={"file": ("n.png", b"x", "image/png")})
    assert tiny.status_code == 400 and tiny.json()["error"]["code"] == "empty_file"


@pytest.mark.asyncio
async def test_a_file_that_is_not_a_notice_is_rejected_by_its_content_not_its_name(client):
    tok = await _join(client)
    r = await client.post(f"/api/me/read?t={tok}", files={"file": ("notice.pdf", b"MZ" + b"\x00" * 400, "application/pdf")})
    assert r.status_code == 415  # an executable renamed to .pdf is detected from its first bytes


@pytest.mark.asyncio
async def test_reads_are_rate_limited_per_student_not_per_address(client):
    first, second = await _join(client), await _join(client)
    codes = [(await client.post(f"/api/me/read?t={first}", files={"file": ("n.png", b"x", "image/png")})).status_code for _ in range(7)]
    assert codes[:6] == [400] * 6 and codes[6] == 429
    # a classmate on the same Wi-Fi address is not blocked by the first student's limit
    assert (await client.post(f"/api/me/read?t={second}", files={"file": ("n.png", b"x", "image/png")})).status_code == 400


@pytest.mark.asyncio
async def test_errors_never_leak_internals(client):
    r = await client.get("/api/me/inbox?t=garbage")
    text = r.text.lower()
    assert "traceback" not in text and "sqlalchemy" not in text and "secret" not in text
    assert set(r.json()["error"]) >= {"code", "message"}

