"""Board Snap: photograph the board, read it, keep it searchable."""

from __future__ import annotations

import logging

from ..config import get_settings
from ..db import sessionmaker
from ..errors import PermanentError
from ..events import bus
from ..models import Job, Snap
from ..providers.media import prepare_document
from ..providers.ocr import run_ocr
from ..storage import get_storage
from .steps import handler

log = logging.getLogger("suchana.snap")


@handler("process_snap")
async def process_snap(job: Job) -> None:
    cfg = get_settings()
    async with sessionmaker()() as s:
        snap = await s.get(Snap, job.payload["snap_id"])
        if snap is None:
            raise PermanentError("snap no longer exists")
        if snap.status == "ready":
            return
        raw = await get_storage().get(snap.source_key)
        doc = prepare_document(raw, max_pages=1, max_side=cfg.snap_max_side_px)
        # Boards are read by Gemini first: it handles handwriting and diagrams well, and keeps Sarvam credit for notices.
        ocr = await run_ocr(doc, "en", prefer="gemini")
        snap.ocr_text, snap.ocr_engine = ocr.text, ocr.engine
        lines = [ln.strip() for ln in ocr.text.splitlines() if ln.strip()]
        snap.summary = " ".join(lines)[:300]
        snap.status = "ready"
        snap.error = None
        await s.commit()
        bus.publish("snap.ready", {"id": snap.id, "student_id": snap.student_id})
