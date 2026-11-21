"""One error shape for every API response, plus error types the pipeline understands.

API errors always look like:
    {"error": {"code": "notice_not_found", "message": "...", "request_id": "..."}}
so the frontend can show a friendly message and screen readers announce it.
"""

from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger("suchana.errors")


class AppError(Exception):
    status_code = 400
    code = "bad_request"

    def __init__(self, message: str, *, code: str | None = None, status_code: int | None = None, detail=None):
        super().__init__(message)
        self.message = message
        if code:
            self.code = code
        if status_code:
            self.status_code = status_code
        self.detail = detail


class NotFound(AppError):
    status_code = 404
    code = "not_found"


class Unauthorized(AppError):
    status_code = 401
    code = "unauthorized"


class Forbidden(AppError):
    status_code = 403
    code = "forbidden"


class Conflict(AppError):
    status_code = 409
    code = "conflict"


class TooManyRequests(AppError):
    status_code = 429
    code = "rate_limited"


# ---------------------------------------------------------------------------
# Pipeline errors: tell the job runner whether a retry can help.
# ---------------------------------------------------------------------------


class PipelineError(Exception):
    """Base class. `retryable` decides whether the job is retried with backoff."""

    retryable = True
    code = "pipeline_error"

    def __init__(self, message: str, *, code: str | None = None, retryable: bool | None = None):
        super().__init__(message)
        if code:
            self.code = code
        if retryable is not None:
            self.retryable = retryable


class PermanentError(PipelineError):
    """Retrying will not help (bad file, unreadable scan, invalid input)."""

    retryable = False
    code = "permanent_error"


def _request_id(request: Request) -> str | None:
    return getattr(request.state, "request_id", None)


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(AppError)
    async def _app_error(request: Request, exc: AppError):
        body = {"code": exc.code, "message": exc.message, "request_id": _request_id(request)}
        if exc.detail is not None:
            body["detail"] = exc.detail
        return JSONResponse({"error": body}, status_code=exc.status_code)

    @app.exception_handler(RequestValidationError)
    async def _validation(request: Request, exc: RequestValidationError):
        fields = [
            {"field": ".".join(str(p) for p in e["loc"] if p != "body"), "message": e["msg"]} for e in exc.errors()
        ]
        return JSONResponse(
            {
                "error": {
                    "code": "validation_error",
                    "message": "Some fields are missing or invalid.",
                    "detail": fields,
                    "request_id": _request_id(request),
                }
            },
            status_code=422,
        )

    @app.exception_handler(StarletteHTTPException)
    async def _http(request: Request, exc: StarletteHTTPException):
        return JSONResponse(
            {"error": {"code": f"http_{exc.status_code}", "message": str(exc.detail), "request_id": _request_id(request)}},
            status_code=exc.status_code,
        )

    @app.exception_handler(Exception)
    async def _unhandled(request: Request, exc: Exception):
        log.exception("unhandled error", extra={"request_id": _request_id(request)})
        return JSONResponse(
            {
                "error": {
                    "code": "internal_error",
                    "message": "Something went wrong on our side. Please try again.",
                    "request_id": _request_id(request),
                }
            },
            status_code=500,
        )
