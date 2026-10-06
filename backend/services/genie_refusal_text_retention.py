"""Hourly purge of expired consented refusal question texts (D-audit-reads-d).

Every report insert and every auditor read already sweeps a bounded batch of
expired texts, but a deployment with no traffic must still forget a question
90 days after it was captured. This loop runs the same sweep once an hour
from the App lifespan: each pass is its own short transaction, run off the
event loop, and repeats while a pass fills its batch. It is system retention:
it writes no audit row, logs counts only, swallows every failure (one
structured event) and never blocks startup or shutdown.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

from backend.services.genie_refusal_report import (
    REFUSAL_TEXT_SWEEP_LIMIT,
    sweep_expired_refusal_texts,
)
from backend.services.lakebase import get_lakebase_client
from backend.services.observability import emit

log = logging.getLogger(__name__)

REFUSAL_TEXT_PURGE_INTERVAL_S = 3600.0
# A backlog drains in batches; this caps one wake-up so a runaway table can
# never pin a worker thread for long.
_MAX_BATCHES_PER_PASS = 50


def purge_expired_refusal_texts(
    client: Any,
    *,
    sweep: Callable[[Any], int] = sweep_expired_refusal_texts,
) -> int:
    """Run sweeps until one returns less than a full batch; return the total."""

    total = 0
    for _ in range(_MAX_BATCHES_PER_PASS):
        with client.transaction() as conn:
            purged = sweep(conn)
        total += purged
        if purged < REFUSAL_TEXT_SWEEP_LIMIT:
            break
    return total


async def refusal_text_purge_once(
    *,
    sweep: Callable[[Any], int] = sweep_expired_refusal_texts,
    client_factory: Callable[[], Any] = get_lakebase_client,
) -> int:
    """One fail-open pass: the purged count, or 0 after a logged failure."""

    try:
        purged = await asyncio.to_thread(purge_expired_refusal_texts, client_factory(), sweep=sweep)
    except Exception as exc:  # noqa: BLE001 - retention must never take the App down
        emit(
            log,
            "refusal_text_purge_failed",
            level=logging.WARNING,
            dependency="lakebase",
            outcome="error",
            error_type=type(exc).__name__,
        )
        return 0
    if purged:
        emit(log, "refusal_text_purged", outcome="purged", purged_count=purged)
    return purged


async def refusal_text_purge_loop(
    interval_s: float = REFUSAL_TEXT_PURGE_INTERVAL_S,
    *,
    sweep: Callable[[Any], int] = sweep_expired_refusal_texts,
    client_factory: Callable[[], Any] = get_lakebase_client,
) -> None:
    """Purge now, then every ``interval_s`` seconds until cancelled."""

    while True:
        await refusal_text_purge_once(sweep=sweep, client_factory=client_factory)
        await asyncio.sleep(interval_s)
