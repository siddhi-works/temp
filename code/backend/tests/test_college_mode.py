"""The college side (staff desk, publishing, walkthroughs, WhatsApp) is off by default and truly absent when off."""

from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient

from app import config


@pytest.mark.asyncio
async def test_college_side_does_not_exist_when_college_mode_is_off(dbready, monkeypatch):
    monkeypatch.setenv("COLLEGE_MODE", "false")
    config.get_settings.cache_clear()
    from app.main import create_app

    async with AsyncClient(transport=ASGITransport(app=create_app()), base_url="http://test") as c:
        setup = {"college_code": "MODERN", "college_name": "Modern College", "admin_name": "Head"}
        assert (await c.post("/api/setup/bootstrap", json=setup)).status_code == 404  # no admin takeover is possible
        assert (await c.get("/api/notices")).status_code == 404
        assert (await c.post("/webhooks/whatsapp", json={})).status_code in (404, 405)
        # ...while everything a student needs still works
        assert (await c.get("/health")).status_code == 200
        assert (await c.post("/api/public/join", json={"lang": "en"})).status_code == 201


@pytest.mark.asyncio
async def test_college_side_mounts_when_college_mode_is_on(client):
    setup = {"college_code": "MODERN", "college_name": "Modern College", "admin_name": "Head"}
    assert (await client.post("/api/setup/bootstrap", json=setup)).status_code == 201
