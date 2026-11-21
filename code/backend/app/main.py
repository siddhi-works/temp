"""Saathi Campus API: application entrypoint.

Builds the FastAPI app, wires the routers, and adds three cross-cutting layers in one place:
security headers, request ids for traceable logs, and a start-up check that refuses to run in
production without real secrets.

Run locally: uvicorn app.main:app --reload
"""

from __future__ import annotations

import logging
import time
import uuid
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware

from .config import get_settings
from .db import dispose_engine, init_models
from .errors import install_error_handlers
from .pipeline.worker import Worker
from .routers import admin, impact, me, notices, public, routes, system, webhook

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
# Hardening headers on every response. microphone=(self) is the one permission the app needs (the deadline recorder).
SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "no-referrer",
    "Permissions-Policy": "camera=(), geolocation=(), microphone=(self)",
    "Cross-Origin-Opener-Policy": "same-origin",
}
# Responses that can contain personal details must never be stored by a browser or a proxy.
NO_STORE_PREFIXES = ("/api/me", "/api/public/join")

log = logging.getLogger("suchana")


@asynccontextmanager
async def lifespan(app: FastAPI):
    s = get_settings()
    if s.is_prod and s.missing_for_prod():
        raise RuntimeError(f"Missing production settings: {', '.join(s.missing_for_prod())}")
    s.storage_dir.mkdir(parents=True, exist_ok=True)
    await init_models()
    worker = None
    if s.worker_enabled:
        worker = Worker()
        worker.start()
    app.state.worker = worker
    log.info("Saathi Campus API ready (env=%s)", s.env)
    try:
        yield
    finally:
        if worker:
            await worker.stop()
        await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Saathi Campus API", version="1.0.0", lifespan=lifespan)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=s.cors_list,
        allow_credentials=False,
        allow_methods=["*"],
        allow_headers=["*"],
    )

    @app.middleware("http")
    async def request_context(request: Request, call_next):
        rid = request.headers.get("x-request-id") or uuid.uuid4().hex[:12]
        request.state.request_id = rid
        start = time.perf_counter()
        response = await call_next(request)
        response.headers["x-request-id"] = rid
        for name, value in SECURITY_HEADERS.items():
            response.headers[name] = value
        if request.url.path.startswith(NO_STORE_PREFIXES):
            response.headers["Cache-Control"] = "no-store"
        if s.is_prod:
            response.headers["Strict-Transport-Security"] = "max-age=31536000; includeSubDomains"
        ms = (time.perf_counter() - start) * 1000
        if request.url.path not in ("/health",) and not request.url.path.startswith("/media/"):
            log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code, ms, rid)
        return response

    install_error_handlers(app)
    # Student-facing endpoints are always on. The college side exists in the code but is mounted only when COLLEGE_MODE=true,
    # so a student-only deployment exposes none of it (including the first-time admin setup).
    routers = [system.router, public.router, me.router, me.public_router]
    if s.college_mode:
        routers += [admin.router, notices.router, notices.review_router, impact.router, webhook.router, routes.router]
    for r in routers:
        app.include_router(r)
    return app


app = create_app()
