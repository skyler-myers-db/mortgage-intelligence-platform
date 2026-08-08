"""Display LTV must never publish a ratio no loan officer can act on.

2026-08-08 UX walk. The blanket-lien guard (audit H5) flagged liens that are
portfolio instruments attributed to one CLIP and fell back to Cotality-modeled
``estimated_cltv`` for display LTV, so a residence would stop rendering a
2,828,497% LTV. It did not finish the job on two counts:

* ``estimated_cltv`` is itself unbounded — live 2026-08-08 it reaches
  2,893,340 across 2,304 silver rows — so 1,939 flagged rows swapped one
  absurd ratio for another.
* The blanket predicate has a floor hole (``> 10x AVM`` **and** ``> $5M``), so
  a lien 15x a $200k AVM is never flagged. 30,818 rows displayed LTVs between
  501% and 8,585%.

Live totals: 12,472 rows rendered an LTV above 500%; after the guard, 0.

The guard is pinned on the SQL text and on the redaction boundary, not on a
canned row: the defect lives in the derivation, and a fixture row can carry
whatever LTV a test asks for.
"""

from __future__ import annotations

import re
from pathlib import Path

from backend.services.pii_redaction import redact_borrower_row

_ROOT = Path(__file__).resolve().parents[2]
_TRANSFORM = (_ROOT / "sql" / "transformations" / "gold_borrower_360.sql").read_text()
_DDL = (_ROOT / "sql" / "ddl" / "gold_borrower_360.sql").read_text()


def _expression(alias: str) -> str:
    """Return the projection text ending in ``AS <alias>,``."""
    normalized = " ".join(_TRANSFORM.split())
    match = re.search(rf"\bAS {re.escape(alias)},", normalized)
    assert match is not None, f"{alias} is not projected by the transformation"
    head = normalized[: match.start()]
    start = head.rfind("CAST(")
    open_paren = head.rfind("( NOT (")
    start = max(start, open_paren)
    assert start != -1, f"could not isolate the {alias} projection"
    return head[start:]


# --- the derivation --------------------------------------------------------


def test_ltv_bounds_both_bases_at_the_same_ceiling() -> None:
    """One threshold, both branches.

    A lien over 5x the valuation is not a property-level loan-to-value
    whatever arithmetic produced it, so the AVM branch and the modeled-CLTV
    fallback must reject it identically. Bounding only the fallback was the
    original bug.
    """
    ltv = _expression("ltv")
    assert "COALESCE(b.estimated_current_lien_balance, 0) <= 5 * b.avm_value" in ltv, (
        "the AVM branch must reject a lien over 5x the valuation — the "
        "blanket-lien predicate's $5M floor lets 15x-under-$5M rows through"
    )
    assert "b.estimated_cltv <= 500" in ltv, (
        "the modeled-CLTV fallback must reject its own absurd values — "
        "estimated_cltv reaches 2,893,340 live"
    )


def test_ltv_requires_a_plausible_valuation_floor() -> None:
    """A sub-$10k valuation is a fossil, not a residence value.

    Same floor the appreciation guard already applies to purchase_amount
    (audit M4) — one number, one meaning. Zero live rows hit it today; this
    keeps it zero when the share next carries one.
    """
    for alias in ("ltv", "equity_pct", "equity_estimate"):
        assert "b.avm_value >= 10000" in _expression(alias), (
            f"{alias} must not derive a ratio from an implausible valuation"
        )


def test_unusable_basis_is_flagged_rather_than_silently_zeroed() -> None:
    """``ltv = 0`` is ambiguous; the flag is what disambiguates it.

    Free-and-clear and no-valuation both land on 0 in a NOT NULL INT column.
    On a contact-prioritization surface, reading "unknown" as "free and clear"
    is the more dangerous of the two lies.
    """
    flag = _expression("ltv_basis_is_unreliable")
    assert "b.avm_value >= 10000" in flag
    assert "COALESCE(b.estimated_current_lien_balance, 0) <= 5 * b.avm_value" in flag
    assert "b.estimated_cltv <= 500" in flag
    # Declared NOT NULL so no consumer has to handle a three-state boolean.
    assert "ltv_basis_is_unreliable   BOOLEAN   NOT NULL" in _DDL


# --- the API boundary ------------------------------------------------------


def _row(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "clip": "1234567890",
        "borrower_id": "B-2Q7X9L4M8N3P1",
        "owner_name_hash": "a1b2c3d4e5f6",
        "city": "Seattle",
        "state": "WA",
        "zip": "98101",
        "avm_value": 640000,
        "current_lien_balance": 410000,
        "current_rate": 6.5,
        "ltv": 64,
    }
    base.update(overrides)
    return base


def test_reliable_basis_publishes_the_ratio() -> None:
    row = redact_borrower_row(_row())
    assert row["ltv"] == 64
    assert row["ltv_basis_is_unreliable"] is False


def test_unreliable_basis_withholds_the_ratio_and_says_so() -> None:
    row = redact_borrower_row(_row(ltv=0, ltv_basis_is_unreliable=True))
    assert row["ltv"] is None, (
        "publishing gold's 0 would read as a paid-off loan; the dossier must "
        "render an explicit unknown instead"
    )
    assert row["ltv_basis_is_unreliable"] is True


def test_withholding_ltv_keeps_the_raw_facts_visible() -> None:
    """Only the derived ratio is withheld — not the inputs behind it.

    Every recommendation traces to source rows, so a suppressed ratio must
    still leave the evidence a reviewer would use to judge it.
    """
    row = redact_borrower_row(
        _row(ltv=0, ltv_basis_is_unreliable=True, avm_value=181000, current_lien_balance=4200000)
    )
    assert row["avm_value"] == 181000
    assert row["current_lien_balance"] == 4200000


def test_missing_flag_column_leaves_the_field_rendering_unchanged() -> None:
    """Pre-extension gold rows and in-process fixtures omit the column."""
    row = redact_borrower_row(_row())
    assert "ltv_basis_is_unreliable" not in _row()
    assert row["ltv"] == 64
