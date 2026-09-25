"""Queue-version contract: an audit-free change signal for the Lead Queue.

Audit 2026-09-21 ``states-09``: a Lead Queue left open looked live while
other approvers changed it, and re-reading the queue to find out would write
a ``VIEW_LEADS`` audit row per poll. This response is the cheap question
instead: an opaque version of the human-decision ledgers behind the queue.
It carries no row, no count, no borrower id and no actor.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class QueueVersionResponse(BaseModel):
    """Opaque version of the Lakebase decision ledgers behind the Lead Queue."""

    model_config = ConfigDict(extra="forbid")

    version: str = Field(
        pattern=r"^[0-9a-f]{32}$",
        description=(
            "32 lowercase hex characters. Changes when an approval, a lead "
            "assignment (including its status), a call disposition or a loan "
            "officer outcome is recorded; equal versions mean none was."
        ),
    )
