"""FastAPI entrypoint. Run: uvicorn app.main:app --reload"""

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
    log.info("Suchana Setu API ready (env=%s)", s.env)
    try:
        yield
    finally:
        if worker:
            await worker.stop()
        await dispose_engine()


def create_app() -> FastAPI:
    s = get_settings()
    app = FastAPI(title="Suchana Setu API", version="0.1.0", lifespan=lifespan)
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
        ms = (time.perf_counter() - start) * 1000
        if request.url.path not in ("/health",) and not request.url.path.startswith("/media/"):
            log.info("%s %s %s %.0fms rid=%s", request.method, request.url.path, response.status_code, ms, rid)
        return response

    install_error_handlers(app)
    for r in (system.router, admin.router, notices.router, notices.review_router, public.router, impact.router, webhook.router, me.router, me.public_router, routes.router):
        app.include_router(r)
    return app


app = create_app()
