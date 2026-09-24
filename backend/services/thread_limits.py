"""Size anyio's default worker-thread limiter (audit ``delivery-09``, S half).

Every sync route, sync dependency and ``FileResponse`` runs on anyio's default
thread limiter, 40 tokens out of the box. The backpressure semaphores alone
admit 24 warehouse + 16 Lakebase + 6 Genie requests (46 > 40), so on a cold
cache blocked single-flight followers could hold every worker thread and stall
``/api/health``, ``/api/session`` and static files behind them.
``MIP_ANYIO_THREAD_TOKENS`` (default 100, 40..400) leaves headroom above those
dependency slots. The limiter belongs to the running event loop, so this must
run inside it: ``backend.main._lifespan`` calls it first thing.
"""
from __future__ import annotations

import anyio.to_thread


def configure_default_thread_limiter(total_tokens: int) -> int:
    """Set the running loop's default thread limiter; returns the new size."""

    anyio.to_thread.current_default_thread_limiter().total_tokens = total_tokens
    return total_tokens


__all__ = ["configure_default_thread_limiter"]
