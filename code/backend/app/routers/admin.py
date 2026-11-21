"""Setup and people: college bootstrap, staff and verifier accounts, student enrolment."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..config import get_settings
from ..db import get_session, utcnow
from ..errors import AppError, Conflict, Forbidden
from ..models import College, StaffRole, StaffUser, Student
from ..pipeline.steps import audit
from ..security import (
    current_staff,
    decrypt_phone,
    encrypt_phone,
    hash_token,
    mask_phone,
    new_staff_token,
    normalize_phone,
    phone_hash,
    require_role,
    sign_student_token,
)

router = APIRouter(prefix="/api", tags=["admin"])


class BootstrapIn(BaseModel):
    college_code: str = Field(min_length=2, max_length=16, pattern=r"^[A-Za-z0-9\-]+$")
    college_name: str = Field(min_length=3, max_length=200)
    district: str | None = "Pune"
    disability_cell_contact: str | None = None
    admin_name: str = Field(min_length=2, max_length=120)
    admin_office: str | None = "College Office"
    setup_token: str | None = None


@router.post("/setup/bootstrap", status_code=201)
async def bootstrap(body: BootstrapIn, s: AsyncSession = Depends(get_session)):
    """One-time setup: allowed only while there are no staff accounts at all (and, if SETUP_TOKEN is set, with that token)."""
    import hmac

    expected = get_settings().setup_token
    if expected and not hmac.compare_digest(expected, body.setup_token or ""):
        raise Forbidden("Setup needs the setup token.", code="setup_token_required")
    if await s.scalar(select(func.count(StaffUser.id))):
        raise Forbidden("Setup is already done. Ask an admin for an account.", code="already_bootstrapped")
    college = College(code=body.college_code.upper(), name=body.college_name, district=body.district, disability_cell_contact=body.disability_cell_contact)
    s.add(college)
    await s.flush()
    token = new_staff_token()
    s.add(StaffUser(college_id=college.id, name=body.admin_name, role=StaffRole.admin, office=body.admin_office, token_hash=hash_token(token)))
    audit(s, "college.bootstrapped", college_id=college.id)
    await s.commit()
    return {"college_id": college.id, "college_code": college.code, "admin_token": token, "note": "Store this token now; it is shown only once."}


@router.get("/me")
async def me(staff: StaffUser = Depends(current_staff)):
    return {
        "id": staff.id,
        "name": staff.name,
        "role": staff.role,
        "office": staff.office,
        "college": {"id": staff.college.id, "code": staff.college.code, "name": staff.college.name},
    }


class StaffIn(BaseModel):
    name: str = Field(min_length=2, max_length=120)
    role: StaffRole = StaffRole.staff
    office: str | None = Field(None, max_length=120)
    rotaract_club: str | None = Field(None, max_length=120)


@router.post("/staff", status_code=201)
async def create_staff(body: StaffIn, admin: StaffUser = Depends(require_role(StaffRole.admin)), s: AsyncSession = Depends(get_session)):
    token = new_staff_token()
    u = StaffUser(college_id=admin.college_id, name=body.name, role=body.role, office=body.office, rotaract_club=body.rotaract_club, token_hash=hash_token(token))
    s.add(u)
    audit(s, "staff.created", college_id=admin.college_id, actor=f"staff:{admin.id}", role=body.role)
    await s.commit()
    return {"id": u.id, "token": token, "note": "Share this sign-in token privately; it is shown only once."}


@router.get("/staff")
async def list_staff(admin: StaffUser = Depends(require_role(StaffRole.admin)), s: AsyncSession = Depends(get_session)):
    rows = (await s.scalars(select(StaffUser).where(StaffUser.college_id == admin.college_id))).all()
    return {"staff": [{"id": u.id, "name": u.name, "role": u.role, "office": u.office, "rotaract_club": u.rotaract_club, "active": u.active} for u in rows]}


@router.delete("/staff/{staff_id}")
async def deactivate_staff(staff_id: str, admin: StaffUser = Depends(require_role(StaffRole.admin)), s: AsyncSession = Depends(get_session)):
    u = await s.get(StaffUser, staff_id)
    if u is None or u.college_id != admin.college_id:
        raise AppError("Staff member not found.", code="staff_not_found", status_code=404)
    if u.id == admin.id:
        raise Conflict("You cannot deactivate yourself.", code="self_deactivate")
    u.active = False
    await s.commit()
    return {"ok": True}


class StudentIn(BaseModel):
    phone: str = Field(min_length=10, max_length=20)
    name: str | None = Field(None, max_length=120)
    preferred_lang: str = Field("en", pattern=r"^(en|mr|hi)$")
    audience_tags: list[str] = Field(default_factory=list)
    consent_confirmed: bool = Field(description="Staff confirm the student agreed to receive notices on WhatsApp")


@router.post("/students", status_code=201)
async def enrol_student(body: StudentIn, staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    if not body.consent_confirmed:
        raise AppError("Ask the student for consent before enrolling them.", code="consent_required")
    try:
        phone = normalize_phone(body.phone)
    except ValueError as exc:
        raise AppError("That phone number does not look right.", code="bad_phone") from exc
    st = await s.scalar(select(Student).where(Student.phone_hash == phone_hash(phone)))
    if st and st.college_id and st.college_id != staff.college_id:
        raise Conflict("This number is registered with another college.", code="other_college")
    if st is None:
        st = Student(phone_hash=phone_hash(phone), phone_enc=encrypt_phone(phone))
        s.add(st)
    st.college_id = staff.college_id
    st.name = body.name or st.name
    st.preferred_lang = body.preferred_lang
    st.audience_tags = [t.strip().upper() for t in body.audience_tags if t.strip()] or None
    st.consent_at = st.consent_at or utcnow()
    st.opted_out_at = None
    audit(s, "student.enrolled", college_id=staff.college_id, actor=f"staff:{staff.id}")
    await s.commit()
    app_link = f"{get_settings().frontend_base_url.rstrip('/')}/me?t={sign_student_token(st.id)}"
    return {
        "id": st.id,
        "phone": mask_phone(phone),
        "app_link": app_link,
        "note": "Ask the student to send HI to the college WhatsApp number once, so notices can reach them. Share the app link with the student only.",
    }


@router.get("/students")
async def list_students(staff: StaffUser = Depends(current_staff), s: AsyncSession = Depends(get_session)):
    rows = (await s.scalars(select(Student).where(Student.college_id == staff.college_id).order_by(Student.created_at))).all()
    out = []
    for st in rows:
        try:
            masked = mask_phone(decrypt_phone(st.phone_enc))
        except RuntimeError:
            masked = "unreadable"
        out.append(
            {
                "id": st.id,
                "name": st.name,
                "phone": masked,
                "preferred_lang": st.preferred_lang,
                "audience_tags": st.audience_tags or [],
                "opted_out": st.opted_out_at is not None,
                "can_receive_now": bool(st.last_inbound_at and (utcnow() - st.last_inbound_at).total_seconds() < 23.5 * 3600),
            }
        )
    return {"students": out}
