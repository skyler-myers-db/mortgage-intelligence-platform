"""The security doc's roles matrix states who may write sales state truthfully.

docs/security-and-compliance.md '## Roles and access' (D-audit-reads-c3)
once marked lead assign, disposition and outcome 'yes' for every tier and said
the sales roster 'carries no extra authorization'. The code says otherwise:
the Lakebase roster role and manager scope authorize those writes
(``backend/services/sales_state_core.py``) and no ``rbac.py`` tier grants
them. These checks keep the matrix row and the functions it cites tied to the
code that enforces them.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.sales_state_writes import _SalesStateWrites

ROOT = Path(__file__).resolve().parents[2]
DOC = ROOT / "docs" / "security-and-compliance.md"
SALES_ROUTER = ROOT / "backend" / "api" / "sales.py"

TIER_COLUMNS = ("Workspace user", "Approver", "Auditor", "Administrator")


def _roles_section() -> str:
    text = DOC.read_text(encoding="utf-8")
    start = text.index("## Roles and access")
    end = text.index("\n## ", start + 1)
    return text[start:end]


def _matrix() -> dict[str, dict[str, str]]:
    rows = [line for line in _roles_section().splitlines() if line.startswith("|")]
    header = [cell.strip() for cell in rows[0].strip("|").split("|")]
    matrix: dict[str, dict[str, str]] = {}
    for line in rows[2:]:
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        assert len(cells) == len(header), line
        matrix[cells[0]] = dict(zip(header[1:], cells[1:], strict=True))
    return matrix


def test_no_rbac_tier_is_granted_the_sales_state_writes() -> None:
    row = _matrix()["Lead assign, disposition, outcome"]

    assert "yes" not in row.values()
    for tier in TIER_COLUMNS:
        assert row[tier] == "only if also on the sales roster", tier
    assert row["Sales team"].startswith("by roster role and scope")
    assert "carries no extra authorization" not in _roles_section()


def test_the_cited_roster_checks_exist_and_the_sales_router_consults_no_rbac_tier() -> None:
    cited = set(re.findall(r"`(?:sales_state_writes\.)?(require_[a-z_]+|log_disposition)`", _roles_section()))

    assert {
        "require_manager_actor",
        "require_assignee_in_scope",
        "require_disposition_scope",
        "require_outcome_scope",
        "log_disposition",
    } <= cited
    for name in cited:
        assert callable(getattr(_SalesStateWrites, name, None)), name
    # The writes are authorized by the roster alone: the router imports no
    # rbac decision, so no tier can grant (or gate) them behind the roster.
    assert "rbac" not in SALES_ROUTER.read_text(encoding="utf-8")
