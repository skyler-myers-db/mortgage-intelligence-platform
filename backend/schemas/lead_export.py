"""Wire contract for the audited Lead Queue CSV export receipt.

The CSV bytes are produced in the browser from the ``/api/leads`` payload the
operator already saw (a server-streamed export was declined on the merits
(wave-5 ruling 2026-09-30): governance-real-data-review.md §2 per-borrower
redistribution, Design System Principle 03, scale and egress of the approval
bypass; tables-08). What the server owns is the LEDGER ENTRY: the client must
obtain a ``LEAD_EXPORT`` receipt before the download starts, and the receipt
carries what an auditor needs to tie a file on disk back to a decision -- the
actor, the filter fingerprint, the exported row count, the SHA-256 of the
file bytes and the SHA-256 of the borrower-id list, the latter recomputed
server-side from the ids the client sends. A loaded-rows export also states
how many borrowers matched the filters (``matching_row_count``), so the
ledger says the file was partial (D-approval-flow-b).
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, Field, field_validator, model_validator

from backend.schemas.common import validate_public_borrower_id
from backend.schemas.lead_query import LEAD_MAX_PAGE_INDEX, MAX_LEAD_LIMIT

SHA256_HEX_PATTERN = re.compile(r"^[0-9a-f]{64}$")
_FILTER_KEY_PATTERN = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
MAX_EXPORT_FILTER_ENTRIES = 64
MAX_EXPORT_FILTER_VALUE_LENGTH = 2048

LeadExportScope = Literal["selected", "loaded"]


def _validate_sha256_hex(value: str, field_name: str) -> str:
    digest = str(value).strip().lower()
    if not SHA256_HEX_PATTERN.fullmatch(digest):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return digest


# Shared with the audit-explorer export receipt (schemas/audit.py), so both
# receipts bound their digests and filter maps identically.
validate_sha256_hex = _validate_sha256_hex


def validate_export_filters(value: dict[str, str]) -> dict[str, str]:
    """Bound an export's query-parameter map: entry count, key shape, value length."""
    if len(value) > MAX_EXPORT_FILTER_ENTRIES:
        raise ValueError("filters carries too many entries")
    for key, raw in value.items():
        if not _FILTER_KEY_PATTERN.fullmatch(key):
            raise ValueError("filters keys must be lowercase query parameter names")
        if not isinstance(raw, str) or len(raw) > MAX_EXPORT_FILTER_VALUE_LENGTH:
            raise ValueError("filters values must be bounded strings")
    return value


class LeadExportReceiptRequest(BaseModel):
    """What the client declares about the file it is about to download."""

    scope: LeadExportScope
    row_count: int = Field(ge=1, le=MAX_LEAD_LIMIT)
    csv_sha256: str = Field(
        description="SHA-256 (hex) of the exact CSV bytes handed to the download.",
    )
    borrower_ids: list[str] = Field(
        min_length=1,
        max_length=MAX_LEAD_LIMIT,
        description="Masked borrower ids in the order the file holds them.",
    )
    borrower_ids_sha256: str = Field(
        description=(
            "SHA-256 (hex) of the compact JSON array of borrower_ids, computed "
            "client-side; the server recomputes it from borrower_ids and refuses "
            "the receipt on a mismatch."
        ),
    )
    filters: dict[str, str] = Field(
        default_factory=dict,
        description=(
            "The Lead Queue query parameters the exported rows were read with "
            "(the same string the CSV's `# filters=` metadata line carries). "
            "Only their fingerprint is written to the ledger."
        ),
    )
    matching_row_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "How many borrowers matched the filters when the file was built "
            "(the CSV's `# matching_rows=` line). Absent when unknown; never "
            "below row_count."
        ),
    )
    # The Lead Queue view the file was built from, AS THE CLIENT DECLARES IT
    # (D-audit-reads-a), and how many of its pages were loaded: the export
    # joins the VIEW_LEADS rows of that view. row_count stays within
    # MAX_LEAD_LIMIT, which covers ten pages of 500.
    lead_view_id: str | None = Field(
        default=None,
        pattern=r"^[0-9a-f]{32}$",
        description="The Lead Queue view the file was built from, as the client declares it.",
    )
    pages_loaded: int | None = Field(
        default=None,
        ge=1,
        le=LEAD_MAX_PAGE_INDEX + 1,
        description="How many pages of that view were loaded when the file was built.",
    )

    @field_validator("csv_sha256")
    @classmethod
    def _csv_digest(cls, value: str) -> str:
        return _validate_sha256_hex(value, "csv_sha256")

    @field_validator("borrower_ids_sha256")
    @classmethod
    def _ids_digest(cls, value: str) -> str:
        return _validate_sha256_hex(value, "borrower_ids_sha256")

    @field_validator("borrower_ids")
    @classmethod
    def _public_borrower_ids(cls, value: list[str]) -> list[str]:
        normalized = [validate_public_borrower_id(item) for item in value]
        if len(set(normalized)) != len(normalized):
            raise ValueError("borrower_ids must not repeat")
        return normalized

    @field_validator("filters")
    @classmethod
    def _bounded_filters(cls, value: dict[str, str]) -> dict[str, str]:
        return validate_export_filters(value)

    @model_validator(mode="after")
    def _matching_covers_the_file(self) -> LeadExportReceiptRequest:
        # A partial file can never claim fewer matching borrowers than it holds.
        if self.matching_row_count is not None and self.matching_row_count < self.row_count:
            raise ValueError("matching_row_count must not be below row_count")
        return self


class LeadExportReceipt(BaseModel):
    """The ledger entry the download waits for."""

    audit_event_id: str
    event_type: Literal["LEAD_EXPORT"] = "LEAD_EXPORT"
    actor: str
    scope: LeadExportScope
    row_count: int
    csv_sha256: str
    borrower_ids_sha256: str
    filter_fingerprint: str
    recorded_at: str
    matching_row_count: int | None = None
