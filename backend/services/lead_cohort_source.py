"""Read one signed Genie cohort back out of Lakebase for replay.

Split from ``backend.api.leads`` when the count-reconciliation work pushed
that module past the size gate (2026-08-11). It lives in ``services`` rather
than ``api`` because it is a Lakebase read, and a router importing another
router breaks the layering test. The route owns request handling; this owns
the single stored-cohort read and its failure contract.
"""

from __future__ import annotations

import json

from fastapi import HTTPException

from backend.services.lakebase import LakebaseError, get_lakebase_client

_COHORT_FILTER_SELECT_SQL = """
SELECT route_filters, row_count
FROM mip_app.genie_cohorts
WHERE cohort_id = %(cohort_id)s
  AND actor_email = %(actor_email)s
LIMIT 1
"""

def _load_cohort_filters(
    cohort_id: str, *, actor: str
) -> tuple[dict[str, object], int | None]:
    """Return the replayable filters and the count the Genie answer stated.

    The stated count is what the user just read on screen. The queue below
    replays only the reviewed geography/segment subset, so the two can
    diverge by orders of magnitude (live 2026-08-10: an answer of 32
    borrowers replays to 1,766). Carrying it here is what lets the response
    say so instead of quietly showing a different population.
    """

    try:
        row = get_lakebase_client().fetchone(
            _COHORT_FILTER_SELECT_SQL,
            {"cohort_id": cohort_id, "actor_email": actor},
        )
    except LakebaseError as exc:
        raise HTTPException(status_code=503, detail="Lakebase temporarily unavailable") from exc
    if row is None:
        raise HTTPException(status_code=404, detail="cohort not found")
    filters_raw = row.get("route_filters") or {}
    if isinstance(filters_raw, str):
        try:
            filters_raw = json.loads(filters_raw)
        except json.JSONDecodeError as exc:
            raise HTTPException(status_code=422, detail="cohort route filters are invalid") from exc
    if not isinstance(filters_raw, dict):
        raise HTTPException(status_code=422, detail="cohort route filters are invalid")
    stated_raw = row.get("row_count")
    stated_count: int | None
    try:
        stated_count = int(stated_raw) if stated_raw is not None else None
    except (TypeError, ValueError):
        stated_count = None
    return filters_raw, stated_count
