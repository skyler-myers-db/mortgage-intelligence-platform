"""Response contracts for the audit-free Lead Queue aggregates.

``GET /leads/count`` and ``GET /leads/facets`` resolve the same filters as
the ranked list but return only totals: no borrower row, no borrower id, and
no VIEW_LEADS audit row, because nothing about an individual borrower is
shown. Bucket values come from closed vocabularies (USPS codes, the reviewed
segment codes, the Portfolio product labels and the approval states).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, Field

LeadFacetDimension = Literal["state", "segment", "product", "approval"]
LEAD_FACET_DIMENSIONS: tuple[LeadFacetDimension, ...] = ("state", "segment", "product", "approval")


@dataclass(frozen=True)
class LeadFacetCounts:
    """What a facet repository answers: closed-vocabulary buckets and their total."""

    total_matching: int
    buckets: list[tuple[str, int]]


class LeadCountResponse(BaseModel):
    """The number of borrowers the Lead Queue filters match."""

    total_matching: int = Field(
        ge=0,
        description="Borrowers matching the filters, the same total GET /leads reports as X-Total-Matching.",
    )


class LeadFacetBucket(BaseModel):
    """One option of a Lead Queue filter menu and how many borrowers it holds."""

    value: str = Field(
        max_length=32,
        description="A closed-vocabulary option: a USPS code, a segment code, a product label or an approval state.",
    )
    count: int = Field(ge=0, description="Borrowers matching every other filter and this option.")


class LeadFacetsResponse(BaseModel):
    """Counts for one filter dimension, with that dimension's own filter dropped."""

    dimension: LeadFacetDimension
    total_matching: int = Field(ge=0, description="Borrowers matching every filter except this dimension's own.")
    buckets: list[LeadFacetBucket] = Field(max_length=64)
