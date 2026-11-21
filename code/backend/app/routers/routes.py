"""Campus Walkthrough: routes recorded step by step by a volunteer (Rotaractor), approved by college staff,
then played to a blind student from a QR code. Nothing is public until approved."""

from __future__ import annotations

import io

from fastapi import APIRouter, Depends, File, Form, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from ..config import get_settings
from ..db import get_session, utcnow
from ..errors import AppError, Conflict, Forbidden, NotFound
from ..models import Route, RouteStep, StaffUser
from ..providers.media import sniff_audio
from ..security import current_staff
from ..storage import get_storage, sha256_bytes

router = APIRouter(tags=["routes"])
MAX_STEPS = 40


def route_json(r: Route, *, staff: bool = False) -> dict:
    base = get_settings().public_base_url.rstrip("/")
    data = {
        "id": r.public_id,
        "title": r.title,
        "from": r.from_place,
        "to": r.to_place,
        "status": r.status,
        "steps": [
            {"id": st.id, "n": i + 1, "text": st.text, "audio_url": f"{base}/media/{st.audio_key}" if st.audio_key else None}
            for i, st in enumerate(r.steps)
        ],
        "url": f"{get_settings().frontend_base_url.rstrip('/')}/r/{r.public_id}",
    }
    if staff:
        data["recorded_by"] = r.recorded_by.name if r.recorded_by else None
        data["approved_by"] = r.approved_by.name if r.approved_by else None
    elif r.approved_by:
        data["verified_by"] = f"{r.approved_by.name}" + (f", {r.approved_by.office}" if r.approved_by.office else "")
    return data


async def _load(s: AsyncSession, public_id: str) -> Route | None:
    return await s.scalar(
        select(Route)
        .where(Route.public_id == public_id)
        .options(selectinload(Route.steps), selectinload(Route.recorded_by), selectinload(Route.approved_by))
        .execution_options(populate_existing=True)  # never serve a stale step list after an edit
    )


async def _own(s: AsyncSession, public_id: str, staff: StaffUser) -> Route:
    r = await _load(s, public_id)
    if r is None or r.college_id != staff.college_id:
        raise NotFound("Route not found.", code="route_not_found")
    return r


class RouteIn(BaseModel):
    title: str = Field(min_length=3, max_length=200)
    from_place: str = Field(min_length=2, max_length=120)
    to_place: str = Field(min_length=2, max_length=120)


@router.post("/api/routes", status_code=201)
async def create_route(body: RouteIn, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    r = Route(college_id=staff.college_id, recorded_by_id=staff.id, **body.model_dump())
    s.add(r)
    await s.commit()
    return route_json(await _load(s, r.public_id), staff=True)


@router.get("/api/routes")
async def list_routes(staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    rows = (
        await s.scalars(
            select(Route)
            .where(Route.college_id == staff.college_id)
            .options(selectinload(Route.steps), selectinload(Route.recorded_by), selectinload(Route.approved_by))
            .order_by(Route.created_at.desc())
        )
    ).all()
    return {"routes": [route_json(r, staff=True) for r in rows]}


@router.post("/api/routes/{public_id}/steps", status_code=201)
async def add_step(
    public_id: str,
    text: str = Form("", max_length=600),
    audio: UploadFile | None = File(None),
    staff: StaffUser = Depends(current_staff),
    s: AsyncSession = Depends(get_session),
):
    """One step = one short voice clip plus its text (the text is what a deaf-blind or noisy-room user reads)."""
    r = await _own(s, public_id, staff)
    if r.status == "published":
        raise Conflict("This route is published. Unpublish it before editing.", code="route_published")
    if len(r.steps) >= MAX_STEPS:
        raise AppError(f"A route can have at most {MAX_STEPS} steps.", code="too_many_steps")
    key = mime = None
    if audio is not None:
        data = await audio.read(3 * 1024 * 1024 + 1)
        if len(data) > 3 * 1024 * 1024:
            raise AppError("Clip is too large (max 3 MB).", code="file_too_large", status_code=413)
        kind = sniff_audio(data)
        if kind is None:
            raise AppError("That does not look like an audio recording.", code="unsupported_audio", status_code=415)
        mime = kind[0]
        key = await get_storage().put(f"routes/{r.public_id}/{sha256_bytes(data)[:20]}{kind[1]}", data)
    text = text.strip()
    if not text and not key:
        raise AppError("Add a recording or type the instruction.", code="empty_step")
    if not text:
        # a clip with no text cannot be reviewed by a sighted approver or read by a deaf-blind user
        raise AppError("Please also type what the clip says, so it can be checked and read.", code="step_text_required")
    nxt = (await s.scalar(select(func.coalesce(func.max(RouteStep.sort), -1)).where(RouteStep.route_id == r.id))) + 1
    s.add(RouteStep(route_id=r.id, sort=nxt, text=text, audio_key=key, audio_mime=mime))
    await s.commit()
    return route_json(await _load(s, public_id), staff=True)


@router.delete("/api/routes/{public_id}/steps/{step_id}")
async def delete_step(public_id: str, step_id: str, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    r = await _own(s, public_id, staff)
    if r.status == "published":
        raise Conflict("This route is published. Unpublish it before editing.", code="route_published")
    st = next((x for x in r.steps if x.id == step_id), None)
    if st is None:
        raise NotFound("Step not found.", code="step_not_found")
    await s.delete(st)
    await s.commit()
    return route_json(await _load(s, public_id), staff=True)


@router.post("/api/routes/{public_id}/publish")
async def publish_route(public_id: str, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    """A second person checks the route: the volunteer who recorded it cannot approve it (admins excepted)."""
    r = await _own(s, public_id, staff)
    if staff.role == "verifier":
        raise Forbidden("A college staff member must approve routes recorded by volunteers.", code="role_forbidden")
    if r.recorded_by_id == staff.id and staff.role != "admin":
        raise Forbidden("Someone else must check this route before it goes live.", code="self_approval")
    if not r.steps:
        raise AppError("Add at least one step first.", code="route_empty")
    r.status, r.approved_by_id, r.approved_at = "published", staff.id, utcnow()
    await s.commit()
    return route_json(await _load(s, public_id), staff=True)


@router.post("/api/routes/{public_id}/unpublish")
async def unpublish_route(public_id: str, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    r = await _own(s, public_id, staff)
    r.status, r.approved_by_id, r.approved_at = "draft", None, None
    await s.commit()
    return route_json(await _load(s, public_id), staff=True)


@router.get("/api/public/routes/{public_id}")
async def public_route(public_id: str, s: AsyncSession = Depends(get_session)):
    r = await _load(s, public_id)
    if r is None or r.status != "published":
        raise NotFound("This route is not available.", code="route_not_found")
    return route_json(r)


@router.get("/api/public/colleges/{college_id}/routes")
async def college_routes(college_id: str, s: AsyncSession = Depends(get_session)):
    rows = (
        await s.scalars(
            select(Route)
            .where(Route.college_id == college_id, Route.status == "published")
            .options(selectinload(Route.steps), selectinload(Route.approved_by))
            .order_by(Route.title)
        )
    ).all()
    return {"routes": [route_json(r) for r in rows]}


@router.get("/api/public/routes/{public_id}/qr.png")
async def route_qr(public_id: str, s: AsyncSession = Depends(get_session)):
    r = await _load(s, public_id)
    if r is None or r.status != "published":
        raise NotFound("This route is not available.", code="route_not_found")
    import qrcode

    img = qrcode.make(route_json(r)["url"], box_size=10, border=4)
    buf = io.BytesIO()
    img.save(buf, format="PNG")
    return Response(buf.getvalue(), media_type="image/png", headers={"Cache-Control": "public, max-age=3600"})
