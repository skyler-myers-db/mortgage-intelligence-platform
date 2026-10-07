"""Admin SSE ingress probe (audit 2026-09-21 ``delivery-04``).

The Genie job-events stream is built only if the Databricks Apps ingress
passes Server-Sent Events through unbuffered and reports a client
disconnect. This router is the measuring instrument for that decision, run
by ``tools/databricks/sse_ingress_spike.py`` against the deployed App:

* ``GET /admin/sse-probe`` streams ``events`` ticks ``interval_ms`` apart, each
  as its OWN ASGI body message (``more_body=True``) with an optional ``: pad``
  comment, ``: keepalive`` comments every ``keepalive_ms`` inside a gap, and a
  final ``event: done``. A concurrent ``receive()`` watcher stops the probe on
  ``http.disconnect``. It holds no dependency slot (backpressure.py) and reads
  no UC or Lakebase.
* ``GET /admin/sse-probe/{probe_id}`` returns what the server recorded for
  one run (events sent, the sequence at which it saw the disconnect, whether
  it completed) from a bounded in-process registry: 32 runs, 10 minutes. The
  App runs one uvicorn process (backend/runtime.py), so the registry is the
  whole truth for that process.

Both routes are admin-only diagnostics, hidden from the OpenAPI schema (not
product contract), and AUDIT EXEMPT: they read and write no product data.
Logs carry the probe id, counts and durations only, never the actor.
"""

from __future__ import annotations

import json
import logging
import threading
import time
from collections import OrderedDict
from collections.abc import Awaitable, Callable, MutableMapping
from contextlib import suppress
from dataclasses import asdict, dataclass
from typing import Annotated, Any

import anyio
from fastapi import APIRouter, HTTPException, Path, Query
from fastapi.responses import Response
from pydantic import BaseModel

from backend.services.observability import emit
from backend.services.rbac import AdminDep

log = logging.getLogger("mip-sse-probe")

router = APIRouter(prefix="/admin/sse-probe", tags=["admin"], include_in_schema=False)

PROBE_ID_PATTERN = r"^[0-9a-f]{16}$"
MAX_PROBE_MS = 300_000
REGISTRY_SIZE = 32
REGISTRY_TTL_S = 600.0

Message = MutableMapping[str, Any]
Receive = Callable[[], Awaitable[Message]]
Send = Callable[[Message], Awaitable[None]]


@dataclass
class ProbeRun:
    events_sent: int = 0
    disconnected_at_seq: int | None = None
    completed: bool = False


class _Registry:
    """The last ``REGISTRY_SIZE`` runs of this process, each for ``REGISTRY_TTL_S``."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._runs: OrderedDict[str, tuple[float, ProbeRun]] = OrderedDict()

    def start(self, probe_id: str) -> ProbeRun:
        run = ProbeRun()
        with self._lock:
            self._prune(time.monotonic())
            self._runs.pop(probe_id, None)
            self._runs[probe_id] = (time.monotonic(), run)
            while len(self._runs) > REGISTRY_SIZE:
                self._runs.popitem(last=False)
        return run

    def get(self, probe_id: str) -> ProbeRun | None:
        with self._lock:
            self._prune(time.monotonic())
            entry = self._runs.get(probe_id)
            return entry[1] if entry else None

    def _prune(self, now: float) -> None:
        for key in [key for key, (at, _) in self._runs.items() if now - at > REGISTRY_TTL_S]:
            del self._runs[key]


REGISTRY = _Registry()


class ProbeStream(Response):
    """A pure-ASGI event stream: one body message per event, disconnect-aware."""

    def __init__(self, *, probe_id: str, events: int, interval_ms: int, pad_bytes: int, keepalive_ms: int) -> None:
        super().__init__(status_code=200, media_type="text/event-stream")
        self.probe_id = probe_id
        self.events = events
        self.interval_s = interval_ms / 1000
        self.pad = (": " + "x" * pad_bytes + "\n").encode() if pad_bytes else b""
        self.keepalive_s = keepalive_ms / 1000

    def _event(self, seq: int) -> bytes:
        data = json.dumps({"seq": seq, "server_ms": int(time.time() * 1000)}, separators=(",", ":"))
        return f"id: {seq}\nevent: tick\ndata: {data}\n".encode() + self.pad + b"\n"

    async def _pause(self, seconds: float, disconnected: anyio.Event, send: Send) -> None:
        deadline = anyio.current_time() + seconds
        while not disconnected.is_set():
            remaining = deadline - anyio.current_time()
            if remaining <= 0:
                return
            step = min(remaining, self.keepalive_s) if self.keepalive_s else remaining
            with anyio.move_on_after(step):
                await disconnected.wait()
            if self.keepalive_s and not disconnected.is_set() and anyio.current_time() < deadline - 0.001:
                await send({"type": "http.response.body", "body": b": keepalive\n\n", "more_body": True})

    async def __call__(self, scope: Any, receive: Receive, send: Send) -> None:
        run = REGISTRY.start(self.probe_id)
        started = time.monotonic()
        emit(log, "sse_probe_started", outcome="started", probe_id=self.probe_id, events=self.events)
        disconnected = anyio.Event()

        async def watch() -> None:
            while True:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    disconnected.set()
                    return

        await send({
            "type": "http.response.start",
            "status": 200,
            "headers": [
                (b"content-type", b"text/event-stream; charset=utf-8"),
                (b"cache-control", b"no-cache, no-transform"),
                (b"x-accel-buffering", b"no"),
            ],
        })
        try:
            async with anyio.create_task_group() as group:
                group.start_soon(watch)
                for seq in range(1, self.events + 1):
                    if disconnected.is_set():
                        break
                    await send({"type": "http.response.body", "body": self._event(seq), "more_body": True})
                    run.events_sent = seq
                    if seq < self.events:
                        await self._pause(self.interval_s, disconnected, send)
                if not disconnected.is_set():
                    await send({"type": "http.response.body", "body": b"event: done\ndata: {}\n\n", "more_body": False})
                    run.completed = True
                group.cancel_scope.cancel()
        except OSError:  # the transport went away mid-send: a disconnect
            disconnected.set()
        if disconnected.is_set():
            run.disconnected_at_seq = run.events_sent
            with anyio.move_on_after(1), suppress(OSError, RuntimeError):
                await send({"type": "http.response.body", "body": b"", "more_body": False})
        emit(
            log,
            "sse_probe_finished",
            outcome="completed" if run.completed else "disconnected",
            duration_ms=round((time.monotonic() - started) * 1000, 1),
            probe_id=self.probe_id,
            events_sent=run.events_sent,
        )


@router.get("", response_model=None)
def sse_probe(
    _actor: AdminDep,
    probe_id: Annotated[str, Query(pattern=PROBE_ID_PATTERN)],
    events: Annotated[int, Query(ge=1, le=120)] = 20,
    interval_ms: Annotated[int, Query(ge=100, le=60_000)] = 500,
    pad_bytes: Annotated[int, Query(ge=0, le=4096)] = 0,
    keepalive_ms: Annotated[int, Query(ge=0, le=30_000)] = 0,
) -> Response:
    """Stream a bounded tick sequence for the ingress spike.

    AUDIT EXEMPT: admin-only transport diagnostic; reads and writes no
    product data, holds no dependency slot.
    """
    if keepalive_ms and keepalive_ms < 1000:
        raise HTTPException(status_code=422, detail="keepalive_ms is 0 or between 1000 and 30000")
    if events * interval_ms > MAX_PROBE_MS:
        raise HTTPException(status_code=422, detail="events * interval_ms exceeds 300000")
    return ProbeStream(
        probe_id=probe_id, events=events, interval_ms=interval_ms, pad_bytes=pad_bytes, keepalive_ms=keepalive_ms
    )


class SseProbeOutcome(BaseModel):
    """What the server recorded for one probe run (not product contract)."""

    events_sent: int
    disconnected_at_seq: int | None = None
    completed: bool


@router.get("/{probe_id}", response_model=SseProbeOutcome)
def sse_probe_outcome(
    _actor: AdminDep,
    probe_id: Annotated[str, Path(pattern=PROBE_ID_PATTERN)],
    response: Response,
) -> SseProbeOutcome:
    """What this process recorded for one probe run.

    AUDIT EXEMPT: admin-only transport diagnostic; an in-process registry read.
    """
    run = REGISTRY.get(probe_id)
    if run is None:
        raise HTTPException(status_code=404, detail="probe not found")
    response.headers["Cache-Control"] = "no-store"
    return SseProbeOutcome(**asdict(run))


__all__ = ["REGISTRY", "ProbeRun", "ProbeStream", "SseProbeOutcome", "router"]
