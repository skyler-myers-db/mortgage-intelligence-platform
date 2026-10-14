"""Print receipt contract: the PRINT_EXPORT row an Export PDF waits for.

Audit responsive-08 (W5d): Borrower 360 and the audit explorer print only
the page already rendered to the reader, and only after the server has
written one ``PRINT_EXPORT`` ledger row for it. The request names the
surface and what is on the page; it never carries row contents. Flat models
only (the API type generator takes the committed schema keywords).
"""

from __future__ import annotations

from typing import Literal, Self

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.schemas.audit import AUDIT_EXPORT_MAX_ROWS
from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead_export import validate_export_filters

PrintSurface = Literal["borrower_360", "audit_ledger"]


class PrintReceiptRequest(BaseModel):
    """What the page declares about the copy it is about to print."""

    surface: PrintSurface
    borrower_id: str | None = Field(
        default=None,
        description="The masked borrower id of the printed dossier (Borrower 360 only).",
    )
    filters: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The audit explorer query parameters the printed page was read with "
            "(audit ledger only). Only their keyed fingerprint is written."
        ),
    )
    row_count: int = Field(ge=1, le=AUDIT_EXPORT_MAX_ROWS)

    @field_validator("filters")
    @classmethod
    def _bounded_filters(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_export_filters(value)

    @model_validator(mode="after")
    def _surface_shape(self) -> Self:
        if self.surface == "borrower_360":
            if self.borrower_id is None:
                raise ValueError("a Borrower 360 print names its borrower")
            self.borrower_id = validate_public_borrower_id(self.borrower_id)
            if self.filters:
                raise ValueError("a Borrower 360 print carries no filters")
            if self.row_count != 1:
                raise ValueError("a Borrower 360 print holds exactly one borrower")
        elif self.borrower_id is not None:
            raise ValueError("an audit ledger print names no borrower")
        return self


class PrintReceipt(BaseModel):
    """The ``PRINT_EXPORT`` ledger entry the print dialog waits for."""

    audit_event_id: str
    event_type: Literal["PRINT_EXPORT"] = "PRINT_EXPORT"
    actor: str
    surface: PrintSurface
    row_count: int
    borrower_id: str | None = None
    filter_fingerprint: str | None = None
    recorded_at: str
