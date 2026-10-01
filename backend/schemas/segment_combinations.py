"""Signal stack schemas (``GET /api/v1/segments/combinations``, audit wow-stage-5).

Where several Cotality signals fire on the same borrower: one row per
non-empty EXACT set of the six core segment codes a borrower carries (at most
63 rows), with the addressable count precomputed in
``mip.gold.segment_combination_rollup`` by the gold refresh and the
contactable subset joined live (the eligibility predicate reads the current
time, so it cannot be a gold column).

Exact rows make every inclusive count exact: the borrowers carrying AT LEAST
a set S are the sum of the exact rows over every superset of S, which the
client sums. That only holds because the vocabulary is the closed six-code
core: the S1.3 overlay codes are ignored, never truncated into a lower bound.
The read is whole-book and audit-free; ``built`` is False until the refresh
has built the table.
"""

from __future__ import annotations

from typing import Self

from pydantic import BaseModel, Field, model_validator

from backend.schemas.lead import SegmentCode

#: The six core segment codes, in the prototype's order (Module 0 Prototype
#: seg-grid). Pinned equal to the geo repository's canonical order prefix and
#: set-equal to GENIE_REPLAY_SEGMENT_CODES.
CORE_SEGMENT_CODES: tuple[SegmentCode, ...] = ("itm", "listed", "permit", "investor", "equity", "retention")


class SegmentCombination(BaseModel):
    """Borrowers carrying EXACTLY these core segment codes (and no other core code)."""

    segment_codes: list[SegmentCode] = Field(
        description="The exact set of core segment codes, distinct and in core order.",
    )
    signal_count: int = Field(ge=1, le=6, description="Number of codes in segment_codes.")
    addressable: int = Field(ge=0, description="Borrowers in gold.borrower_360 carrying exactly this set.")
    contactable: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The contact-eligible subset of addressable (live eligibility predicate, clamped "
            "to addressable). None means not reported, never zero."
        ),
    )

    @model_validator(mode="after")
    def _exact_core_set(self) -> Self:
        codes = list(self.segment_codes)
        if not codes or len(set(codes)) != len(codes):
            raise ValueError("segment_codes must be a non-empty set of distinct codes")
        if [code for code in CORE_SEGMENT_CODES if code in codes] != codes:
            raise ValueError("segment_codes must be core codes in core order")
        if self.signal_count != len(codes):
            raise ValueError("signal_count must equal the number of segment_codes")
        if self.contactable is not None and self.contactable > self.addressable:
            raise ValueError("contactable must not exceed addressable")
        return self


class SegmentCombinationProvenance(BaseModel):
    """Evidence manifest for the signal stack."""

    source: str = Field(description="The precomputed gold table the exact rows come from.")
    contactable_source: str = Field(description="Where the live contactable subset comes from.")
    refreshed_at: str | None = Field(default=None, description="Refresh anchor of the gold rows.")
    note: str


class SegmentCombinationResponse(BaseModel):
    built: bool = Field(description="False until the gold refresh has built mip.gold.segment_combination_rollup.")
    core_codes: list[SegmentCode] = Field(description="The six core segment codes the rows are drawn from, in core order.")
    combinations: list[SegmentCombination] = Field(
        description="Exact combinations, largest addressable first, then by combination key.",
    )
    provenance: SegmentCombinationProvenance
