"""The Admin 'Audit coverage' sentence matches what the server actually audits.

D-shell-deviations-e1 (audit critic-05): the Deployment readiness panel
(frontend/src/components/admin/BuyerReadinessPanel.tsx) states what the audit
ledger records. Its old line said page views and filter changes were not
audited, but every borrower-level read writes a server-owned row (ranked lead
lists with their filters, Borrower 360 and its proof, offer recommendations,
outreach drafts). The corrected sentence must keep naming borrower-level
reads as audited, and the server must keep owning the event types it cites.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.audit_event_types import SERVER_OWNED_AUDIT_EVENT_TYPES

ROOT = Path(__file__).resolve().parents[2]
PANEL = ROOT / "frontend" / "src" / "components" / "admin" / "BuyerReadinessPanel.tsx"

_COVERAGE_RE = re.compile(
    r"label:\s*'Audit coverage',.*?detail:\s*'(?P<detail>[^']+)'",
    re.DOTALL,
)

# The reads and actions the sentence says are audited, by their ledger code.
_CITED_EVENT_TYPES = frozenset(
    {
        "VIEW_LEADS",
        "VIEW_BORROWER",
        "VIEW_BORROWER_PROOF",
        "RECOMMEND_OFFER",
        "DRAFT_OUTREACH",
        "RUN_GENIE",
        "PROPERTY_LOOKUP",
        "LEAD_EXPORT",
    }
)


def _coverage_detail() -> str:
    match = _COVERAGE_RE.search(PANEL.read_text(encoding="utf-8"))
    assert match, "BuyerReadinessPanel.tsx states an Audit coverage detail"
    return match.group("detail")


def _not_audited_clause(detail: str) -> str:
    assert "Not audited:" in detail, "the sentence names what is not audited"
    return detail.split("Not audited:", 1)[1]


def test_the_not_audited_clause_never_claims_borrower_reads_or_filters_go_unaudited() -> None:
    clause = _not_audited_clause(_coverage_detail()).lower()

    for phrase in ("page views", "filter changes", "borrower"):
        assert phrase not in clause, f"'Not audited' must not list {phrase!r}: the server audits it"


def test_the_sentence_names_borrower_level_reads_as_audited() -> None:
    audited = _coverage_detail().split("Not audited:", 1)[0].lower()

    for phrase in ("borrower-level reads", "ranked lead lists", "borrower 360", "outreach drafts", "property lookups"):
        assert phrase in audited


def test_the_server_owns_every_event_type_the_sentence_cites() -> None:
    assert _CITED_EVENT_TYPES <= SERVER_OWNED_AUDIT_EVENT_TYPES


def test_the_clause_check_is_not_vacuous() -> None:
    """The old sentence's shape is what the first test refuses."""
    old = "Audited: approvals. Not audited: page views and filter changes."
    clause = _not_audited_clause(old).lower()
    assert "page views" in clause and "filter changes" in clause
