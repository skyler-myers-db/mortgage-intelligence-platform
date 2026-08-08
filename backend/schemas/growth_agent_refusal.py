"""Governed refusal vocabulary for growth co-pilot prompt guards.

The guard battery in ``backend.schemas.growth_agent`` used to raise one
catch-all ``ValueError`` for ~17 distinct guard families, so a fair-lending
targeting attempt ("Rank borrowers by race for our next campaign.") reached
the lender as a PII/validation complaint while the identical prompt on Ask
Genie returned the ECOA/FHA template (persona audit, 2026-08-07).

Two values travel together from here:

* ``code`` -- a stable machine token. It is the audit ``refusal_reason``, it
  is what tests pin, and it shares the Genie refusal vocabulary in
  ``backend/services/audit_store.py`` so one compliance query
  (``refusal_reason = 'protected_class'``) finds copilot *and* Ask Genie
  refusals of the same family.
* ``reason`` -- lender-facing copy. Free to be re-worded without breaking the
  pinned code.

``unreviewed_criterion`` keeps the historical catch-all sentence verbatim: it
is the honest fail-closed state ("these criteria are not in the reviewed
vocabulary"), not a fair-lending finding.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any, Literal

GrowthPromptRefusalCode = Literal[
    "protected_class",
    "instruction_override",
    "cross_lender_targeting",
    "unavailable_source",
    "pii_request",
    "unreviewed_criterion",
]

GROWTH_PROMPT_REFUSAL_REASONS: dict[GrowthPromptRefusalCode, str] = {
    "protected_class": (
        "prompt cannot segment, score, rank, or target borrowers using protected-class "
        "attributes or proxies; use reviewed mortgage, lien, equity, segment, and offer criteria"
    ),
    "instruction_override": (
        "prompt cannot override system, developer, or safety instructions or reach raw "
        "tables and SQL; ask for a reviewed mortgage-growth objective"
    ),
    "cross_lender_targeting": (
        "prompt cannot target a named competitor's customers; use the reviewed "
        "competitor-recapture workflow over governed lien and recapture signals"
    ),
    "unavailable_source": (
        "prompt asks for a signal this workspace does not carry; use reviewed mortgage, "
        "lien, equity, listing, segment, and offer signals"
    ),
    "pii_request": (
        "prompt must not carry borrower names, street addresses, or raw source identifiers; "
        "describe the cohort with non-PII mortgage-growth criteria"
    ),
    # Historical catch-all sentence, now scoped to the one family it describes.
    "unreviewed_criterion": "prompt must use reviewed, non-PII mortgage-growth criteria",
}


@dataclass(frozen=True)
class GrowthPromptRefusal:
    """A refused objective: the guard family plus its lender-facing reason."""

    code: GrowthPromptRefusalCode
    reason: str
    question_hash: str


class GrowthPromptRefusalError(ValueError):
    """Reviewed-objective rejection that names the guard family that fired.

    Raised inside pydantic validators, so it arrives at
    ``backend.main._request_validation_handler`` inside the pydantic error
    ``ctx``. The handler reads the family from there rather than re-scanning
    the submitted prompt, which keeps prompt text out of the refusal path
    entirely -- only ``question_hash`` (a truncated digest of the normalized
    objective) is ever persisted or logged.
    """

    def __init__(self, code: GrowthPromptRefusalCode, *, question_hash: str) -> None:
        self.refusal = GrowthPromptRefusal(
            code=code,
            reason=GROWTH_PROMPT_REFUSAL_REASONS[code],
            question_hash=question_hash,
        )
        super().__init__(self.refusal.reason)

    @property
    def code(self) -> GrowthPromptRefusalCode:
        return self.refusal.code


def growth_prompt_refusal_from_errors(errors: Iterable[Any]) -> GrowthPromptRefusal | None:
    """Recover the guard family from a pydantic/FastAPI error list.

    Returns ``None`` for ordinary malformed-body errors so only real guard
    refusals take the audited branch.
    """

    for item in errors:
        if not isinstance(item, dict):
            continue
        context = item.get("ctx")
        error = context.get("error") if isinstance(context, dict) else None
        if isinstance(error, GrowthPromptRefusalError):
            return error.refusal
    return None
