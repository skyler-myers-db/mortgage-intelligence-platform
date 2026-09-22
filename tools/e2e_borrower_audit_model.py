"""Result containers and gold-layer threshold constants for the borrower E2E audit.

Split out of ``tools/e2e_borrower_audit.py`` (2026-09-08) so the fetch,
recompute, compare, and report modules share one model without importing the
CLI. Every definition moved verbatim; see
``docs/maintenance/file-size-refactor-plan.md``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

# ---------------------------------------------------------------------------
# Constants mirroring the gold-layer CTAS (see sql/transformations/
# gold_borrower_360.sql). The audit tool holds them inline so a drift in
# the SQL literal is detectable here by construction.
# ---------------------------------------------------------------------------

DEFAULT_MIN_SPREAD_BPS = 75
DEFAULT_MIN_EQUITY_PCT = 15
DEFAULT_HELOC_EQUITY_MIN = 35
DEFAULT_CASHOUT_EQUITY_MIN = 25
DEFAULT_RETENTION_MIN_SPREAD = 50

# ---------------------------------------------------------------------------
# Result containers
# ---------------------------------------------------------------------------


@dataclass
class Mismatch:
    """One field-level disagreement between two surfaces.

    ``surface_a`` / ``surface_b`` are the two surfaces being compared
    (e.g. ``raw_recomputed`` vs ``gold``). ``expected`` is the value
    from the earlier/ground-truth surface; ``actual`` is the later one.
    """

    clip: str
    borrower_id: str
    surface_a: str
    surface_b: str
    field: str
    expected: Any
    actual: Any
    notes: str = ""


@dataclass
class ClipAudit:
    """Per-CLIP audit bundle."""

    clip: str
    borrower_id: str
    state: str
    opportunity_score_bucket: str  # "high" / "mid" / "low"
    raw_inputs: dict[str, Any] = field(default_factory=dict)
    raw_recomputed: dict[str, Any] = field(default_factory=dict)
    gold: dict[str, Any] = field(default_factory=dict)
    api: dict[str, Any] = field(default_factory=dict)
    mismatches: list[Mismatch] = field(default_factory=list)
    # raw->silver freshness/coercion drift -- informational, does not
    # block the audit pass/fail judgement (gold correctness depends on
    # silver, not raw, so a raw-vs-silver drift is a data-engineering
    # question rather than a gold-arithmetic bug).
    raw_vs_silver: list[Mismatch] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return not self.mismatches
