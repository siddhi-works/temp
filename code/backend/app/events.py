"""In-process event bus for live UI updates over Server-Sent Events.

Single-instance by design (hackathon + first pilot). To scale to several API
instances, swap the publish side for Postgres LISTEN/NOTIFY; subscribers stay
the same.
"""

from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass, field
from typing import Any

from .db import utcnow

log = logging.getLogger("suchana.events")


@dataclass(eq=False)  # identity hashing: each subscriber is a distinct live connection
class Subscriber:
    college_id: str | None
    queue: asyncio.Queue = field(default_factory=lambda: asyncio.Queue(maxsize=200))


class EventBus:
    def __init__(self) -> None:
        self._subs: set[Subscriber] = set()

    def subscribe(self, college_id: str | None) -> Subscriber:
        sub = Subscriber(college_id)
        self._subs.add(sub)
        return sub

    def unsubscribe(self, sub: Subscriber) -> None:
        self._subs.discard(sub)

    def publish(self, event: str, data: dict[str, Any], college_id: str | None = None) -> None:
        msg = {"event": event, "data": data, "college_id": college_id, "at": utcnow().isoformat()}
        for sub in list(self._subs):
            if sub.college_id and college_id and sub.college_id != college_id:
                continue
            try:
                sub.queue.put_nowait(msg)
            except asyncio.QueueFull:
                # A slow client must never block the pipeline; it will resync on reconnect.
                log.warning("dropping event for slow subscriber")

    @staticmethod
    def format_sse(msg: dict) -> str:
        return f"event: {msg['event']}\ndata: {json.dumps(msg, default=str)}\n\n"

    @property
    def subscriber_count(self) -> int:
        return len(self._subs)


bus = EventBus()
