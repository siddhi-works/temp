"""Health, readiness and live events."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, Query, Request
from fastapi.responses import JSONResponse, StreamingResponse
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session, sessionmaker
from ..events import bus
from ..models import Job, JobStatus, StaffUser
from ..providers.base import breaker_states, spent_paise
from ..security import hash_token

router = APIRouter(tags=["system"])


@router.get("/health")
async def health():
    """Liveness: the process is up."""
    return {"ok": True}


@router.get("/ready")
async def ready(request: Request):
    """Readiness: database reachable, worker alive, which providers are configured."""
    s = get_settings()
    checks: dict = {}
    try:
        async with sessionmaker()() as session:
            await session.execute(text("SELECT 1"))
            dead = await session.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.dead))
            queued = await session.scalar(select(func.count(Job.id)).where(Job.status == JobStatus.queued))
        checks["database"] = "ok"
        checks["jobs"] = {"queued": queued, "dead": dead}
        checks["sarvam_spent_rupees"] = round(await spent_paise("sarvam") / 100, 2)
    except Exception as exc:
        checks["database"] = f"error: {type(exc).__name__}"
    worker = getattr(request.app.state, "worker", None)
    checks["worker"] = "running" if worker and worker.running else ("disabled" if not s.worker_enabled else "stopped")
    checks["providers"] = {
        "sarvam": bool(s.sarvam_api_key),
        "gemini": bool(s.gemini_api_key),
        "google_tts": bool(s.google_tts_api_key),
        "whatsapp": bool(s.whatsapp_token and s.whatsapp_phone_number_id),
    }
    checks["circuits"] = breaker_states()
    if s.is_prod:
        checks["config_problems"] = s.missing_for_prod()
    ok = checks["database"] == "ok" and checks["worker"] != "stopped"
    return JSONResponse({"ready": ok, **checks}, status_code=200 if ok else 503)


@router.get("/api/events")
async def events(request: Request, token: str = Query(..., description="staff token (EventSource cannot send headers)"), session: AsyncSession = Depends(get_session)):
    staff = await session.scalar(select(StaffUser).where(StaffUser.token_hash == hash_token(token), StaffUser.active.is_(True)))
    if staff is None:
        return JSONResponse({"error": {"code": "invalid_token", "message": "Sign in again."}}, status_code=401)
    college_id = staff.college_id
    await session.close()  # do not hold a DB connection for the life of the stream

    async def stream():
        sub = bus.subscribe(college_id)
        try:
            yield "retry: 3000\n\n"
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(sub.queue.get(), timeout=15)
                    yield bus.format_sse(msg)
                except asyncio.TimeoutError:
                    yield ": keep-alive\n\n"  # stops proxies from closing an idle stream
        finally:
            bus.unsubscribe(sub)

    return StreamingResponse(stream(), media_type="text/event-stream", headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
