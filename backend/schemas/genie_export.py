"""Wire contract for the audited Genie answer CSV export receipt.

The CSV is built in the browser from rows the Genie answer already holds (at
most 5,000; no server-streamed or full-cohort export, an owner decision in the
2026-09-21 audit register, genie-06). What the server owns is the LEDGER
ENTRY: the browser asks for a ``GENIE_ANSWER_EXPORT`` receipt before the
download starts and downloads nothing unless it arrives. The declaration
carries identifiers, counts and two digests only; ``extra='forbid'`` keeps
question text, cell values and any other free text off the wire.
"""

from __future__ import annotations

import re
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

#: Parity with the Lead Queue export cap (``MAX_LEAD_LIMIT``).
MAX_GENIE_EXPORT_ROWS = 5_000

GenieExportScope = Literal["answer", "section"]

_GENIE_ID_PATTERN = r"^[A-Za-z0-9-]{1,128}$"
_SHA256_HEX = re.compile(r"^[0-9a-f]{64}$")


def _sha256_hex(value: str, field_name: str) -> str:
    if not _SHA256_HEX.fullmatch(value):
        raise ValueError(f"{field_name} must be a lowercase SHA-256 hex digest")
    return value


class GenieAnswerExportReceiptRequest(BaseModel):
    """What the browser declares about the file it is about to download."""

    model_config = ConfigDict(extra="forbid")

    conversation_id: str = Field(pattern=_GENIE_ID_PATTERN)
    message_id: str = Field(pattern=_GENIE_ID_PATTERN)
    scope: GenieExportScope
    row_count: int = Field(
        ge=1,
        le=MAX_GENIE_EXPORT_ROWS,
        description="Rows the file holds: the rows the answer (or section) carries.",
    )
    answer_row_count: int | None = Field(
        default=None,
        ge=0,
        description=(
            "The row count the answer (scope 'answer') or section (scope 'section') "
            "reports; above row_count only for a History replay that kept fewer rows."
        ),
    )
    csv_sha256: str = Field(description="SHA-256 (hex) of the exact CSV text handed to the download.")
    columns_sha256: str = Field(
        description="SHA-256 (hex) of the compact JSON array of the file's column keys."
    )

    @field_validator("csv_sha256")
    @classmethod
    def _csv_digest(cls, value: str) -> str:
        return _sha256_hex(value, "csv_sha256")

    @field_validator("columns_sha256")
    @classmethod
    def _columns_digest(cls, value: str) -> str:
        return _sha256_hex(value, "columns_sha256")


class GenieAnswerExportReceipt(BaseModel):
    """The ledger entry the download waits for."""

    audit_event_id: str
    event_type: Literal["GENIE_ANSWER_EXPORT"] = "GENIE_ANSWER_EXPORT"
    actor: str
    scope: GenieExportScope
    row_count: int
    csv_sha256: str
    columns_sha256: str
    recorded_at: str
