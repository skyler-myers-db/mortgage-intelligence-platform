"""Lead Queue bulk-run ETA: its floor is the backend's mutation budget.

2026-09-21 UI/UX audit, tables-07 / states-08. A bulk approve used to show
only "Approving..." for minutes. The run now states "about M min left", and
M never drops below the rate the server will actually accept writes at:
remaining POSTs / MUTATION_BUDGET_PER_MINUTE, where an unsampled approve
costs two POSTs (the governed draft, then the approve) and a sampled
approve or a reject costs one.

These tests pin the two facts that make that floor honest, so a change on
either side fails here instead of letting the ETA promise a pace the
backpressure controller would answer with 429s:

1. the frontend constant equals the backend's default
   ``mip_rate_limit_mutation_per_minute``;
2. the three POSTs a bulk row sends (draft, approve, reject) all draw on
   that one "mutation" bucket.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.config.settings import Settings
from backend.services.backpressure import BackpressureController

_REPO_ROOT = Path(__file__).resolve().parents[2]
_CONSTANTS = _REPO_ROOT / "frontend" / "src" / "components" / "mortgage" / "LeadTable.constants.ts"


def _frontend_budget() -> int:
    match = re.search(
        r"^export const MUTATION_BUDGET_PER_MINUTE = (\d+);$",
        _CONSTANTS.read_text(encoding="utf-8"),
        re.MULTILINE,
    )
    assert match, "LeadTable.constants.ts must export MUTATION_BUDGET_PER_MINUTE as an integer literal"
    return int(match.group(1))


def test_frontend_eta_floor_uses_the_backend_mutation_budget() -> None:
    backend_default = Settings.model_fields["mip_rate_limit_mutation_per_minute"].default
    assert _frontend_budget() == backend_default == 120


def test_every_bulk_row_post_draws_on_the_mutation_bucket() -> None:
    controller = BackpressureController()
    for path in ("/api/outreach/draft", "/api/outreach/approve", "/api/outreach/reject"):
        budget = controller.classify("POST", path)
        assert budget is not None, path
        assert budget.scope == "mutation", path
