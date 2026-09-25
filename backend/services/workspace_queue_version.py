"""Audit-free queue version over the Lakebase decision ledgers.

Audit 2026-09-21 ``states-09``. The Lead Queue polls this about once a minute
while it is mounted and visible, and shows "Queue updated · Refresh" when the
version moves; it never re-reads ``/api/leads`` by itself (that read writes a
``VIEW_LEADS`` audit row).

What it reads: ONE Lakebase SELECT, one aggregate pass per table, over the
human-decision ledgers only (``mip_app`` schema, never Unity Catalog):

* ``mip_app.approvals``        count, max(decided_at)
* ``mip_app.lead_assignments`` count, max(greatest(assigned_at, released_at,
  status_updated_at)), so an assignment status change moves it too
* ``mip_app.call_dispositions`` count, max(created_at)
* ``mip_app.feedback`` rows WITH an assignment_id (loan-officer outcomes)
  count, max(recorded_at)

Counts catch a delete that leaves the maximum unchanged. The version is
``sha256("mip.queue-version.v1|" + the eight values)[:32]``: opaque, no row,
no count, no borrower id and no actor leaves the service.

What it deliberately is not:

* It writes NO audit row and resolves no audit store: a poll is not a read of
  borrower data.
* It is global, not tenant-scoped: this is a single-tenant deploy and these
  tables carry no tenant column.
* Documented gaps: outreach delivery status (activation outbox) and
  CRM-imported ``lead_outcomes`` reach the queue through the lifecycle sync
  job, not these ledgers, so they do not move the version.

Cost: four aggregate scans per 30 s per worker (process-local TTL cache,
never serving a stale value after an error). ``approvals`` has no
``decided_at`` index today; acceptable at Module 0 volume, and an index is a
later Lakebase-migration candidate.
"""

from __future__ import annotations

import hashlib
import logging
from collections.abc import Callable
from datetime import datetime
from typing import Any, Protocol

from backend.services.lakebase import get_lakebase_client
from backend.services.observability import emit
from backend.services.resilience_cache import TTLCache

logger = logging.getLogger(__name__)

# The Lead Queue waits out this TTL before a read may absorb the reader's own
# write (frontend/src/lib/queueVersion.ts QUEUE_VERSION_SERVER_TTL_MS, pinned
# equal by tests/unit/test_workspace_queue_version.py): until then a read can
# be answered from a fill that predates the write, on any worker.
QUEUE_VERSION_TTL_S = 30.0
_CACHE_KEY = "mip.queue-version"
_VERSION_PREFIX = "mip.queue-version.v1|"

QUEUE_VERSION_SQL = """
WITH approvals AS (
    SELECT COUNT(*) AS n, MAX(decided_at) AS latest
    FROM mip_app.approvals
),
assignments AS (
    SELECT COUNT(*) AS n, MAX(GREATEST(assigned_at, released_at, status_updated_at)) AS latest
    FROM mip_app.lead_assignments
),
dispositions AS (
    SELECT COUNT(*) AS n, MAX(created_at) AS latest
    FROM mip_app.call_dispositions
),
outcomes AS (
    SELECT COUNT(*) AS n, MAX(recorded_at) AS latest
    FROM mip_app.feedback
    WHERE assignment_id IS NOT NULL
)
SELECT
    approvals.n AS approvals_count,
    approvals.latest AS approvals_latest,
    assignments.n AS assignments_count,
    assignments.latest AS assignments_latest,
    dispositions.n AS dispositions_count,
    dispositions.latest AS dispositions_latest,
    outcomes.n AS outcomes_count,
    outcomes.latest AS outcomes_latest
FROM approvals, assignments, dispositions, outcomes
"""

VERSION_INPUTS = (
    "approvals_count",
    "approvals_latest",
    "assignments_count",
    "assignments_latest",
    "dispositions_count",
    "dispositions_latest",
    "outcomes_count",
    "outcomes_latest",
)


class _SupportsFetchOne(Protocol):
    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None: ...


def _canonical(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def queue_version_from_row(row: dict[str, Any] | None) -> str:
    """The opaque version of one aggregate row (an empty ledger set hashes too)."""

    values = [_canonical((row or {}).get(name)) for name in VERSION_INPUTS]
    digest = hashlib.sha256((_VERSION_PREFIX + "|".join(values)).encode("utf-8")).hexdigest()
    return digest[:32]


_CACHE = TTLCache(max_entries=4)


class QueueVersionService:
    """Reads the queue version through a short TTL cache.

    The Lakebase client is resolved on a cache miss, inside the call, so a
    missing configuration surfaces as ``LakebaseError`` where the router maps
    it to a 503 (never while FastAPI resolves dependencies).
    """

    def __init__(
        self,
        client_factory: Callable[[], _SupportsFetchOne],
        *,
        cache: TTLCache | None = None,
        ttl_s: float = QUEUE_VERSION_TTL_S,
    ) -> None:
        self._client_factory = client_factory
        self._cache = cache if cache is not None else _CACHE
        self._ttl_s = ttl_s

    def current(self) -> str:
        version = self._cache.get_or_set(
            _CACHE_KEY,
            self._read,
            ttl_s=self._ttl_s,
            stale_if_error=False,
        )
        return str(version)

    def _read(self) -> str:
        row = self._client_factory().fetchone(QUEUE_VERSION_SQL)
        version = queue_version_from_row(row)
        emit(logger, "queue_version_read", dependency="lakebase", outcome="ok")
        return version


def get_queue_version_service() -> QueueVersionService:
    return QueueVersionService(get_lakebase_client)
