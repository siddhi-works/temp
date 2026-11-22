"""Where a student's token is read from.

The website sends it in the Authorization header (`Bearer <token>`), so it never appears in a URL, where proxies, edge logs
and browser history would keep it. The old `?t=` form still works for links that already exist (for example the WhatsApp link).
"""

from __future__ import annotations

from fastapi import Header, Query

from .errors import Unauthorized


def student_token(
    t: str | None = Query(None, description="signed token (older links); prefer the Authorization header"),
    authorization: str | None = Header(None),
) -> str:
    if authorization and authorization[:7].lower() == "bearer ":
        return authorization[7:].strip()
    if t:
        return t
    raise Unauthorized("Please open Samyukta again and tap Start.", code="token_required")
