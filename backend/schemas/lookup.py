"""API contracts for the governed property loan lookup.

Boundary: v1 is a SHARE-SCOPED, EXACT-after-canonicalization lookup against the
refreshed Cotality share -- NOT Cotality's CLIP mastering / resolution service.
The response NEVER echoes ``address_line`` (the caller-supplied street string);
every surfaced identifier is masked or already-public.
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from backend.schemas.lead import SegmentCode


class PropertyLoanLookupRequest(BaseModel):
    """Request body for ``POST /api/v1/lookup/property-loan``.

    Field constraints are simple length / digit bounds only -- no PII-shaped
    regex that could be reflected back through the 422 error ``input`` field.
    (The app-wide RequestValidationError handler already strips ``input`` /
    ``ctx`` from 422 bodies, so even a rejected ZIP never echoes the raw value.)
    """

    # Street address the caller typed. Min 4 chars ("1 Rd"), max 200. No
    # pattern -- the value is hashed, never stored or echoed.
    address_line: str = Field(min_length=4, max_length=200)
    # 5-digit ZIP. Length-pinned to 5; digit-shape is validated in-route with a
    # fixed message so a bad ZIP does not reflect the submitted value.
    zip5: str = Field(min_length=5, max_length=5)
    city: str | None = Field(default=None, max_length=120)
    state: str | None = Field(default=None, max_length=2)


class PropertyLoanLookupLoan(BaseModel):
    """The loan facts surfaced on a match. All fields are public-safe."""

    lender_brand: str | None = None
    current_rate: float
    current_lien_balance: int
    has_open_lien: bool
    ltv: int


class PropertyLoanLookupResponse(BaseModel):
    """Response for the property loan lookup.

    Never contains ``address_line``. ``clip_ref`` / ``owner_link_ref`` /
    ``borrower_id`` are masked; ``lead_score`` / ``segment`` / ``loan`` /
    ``dossier_path`` are populated only when the property maps to a governed
    borrower row.
    """

    matched: bool
    match_basis: Literal["exact_normalized_address_zip"] = "exact_normalized_address_zip"
    clip_ref: str | None = None
    owner_link_ref: str | None = None
    borrower_id: str | None = None
    lead_score: int | None = None
    segment: list[SegmentCode] | None = None
    loan: PropertyLoanLookupLoan | None = None
    dossier_path: str | None = None
    audit_event_id: str


__all__ = [
    "PropertyLoanLookupLoan",
    "PropertyLoanLookupRequest",
    "PropertyLoanLookupResponse",
]
