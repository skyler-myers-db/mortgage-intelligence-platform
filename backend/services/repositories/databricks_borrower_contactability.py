"""Batched gold contactability for a list of borrower ids (D-shell-deviations-a).

The Follow-ups tray checks every open item against gold before it shows it as
callable. It does so with the Lead Queue's own pair, never a hand-rolled third
rule: ``GoldEligibilityService.evaluate`` over the seven transport columns, AND
``eligible_sql_predicate`` evaluated by the warehouse on the same row. Both are
carried on :class:`BorrowerContactabilityRow`, which is always constructed with
all eight fields passed explicitly, so ``evaluate`` sees the three required
contactability fields in ``model_fields_set`` and can never read a row as
"not configured" because a column defaulted.

Ids are normalised and chunked exactly like ``existing_borrower_ids``: a
malformed id is dropped rather than bound into SQL, and one statement carries
at most 250 ids.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Any

from pydantic import BaseModel, field_validator

from backend.services.databricks_sql_helpers import qualify
from backend.services.eligibility import eligible_sql_predicate
from backend.services.repositories.databricks_lead_cohort_support import (
    LeadCohortQuerySupport,
)

_TRUE_TEXT = frozenset({"true", "1", "t", "yes"})

#: Bind-parameter ceiling per statement, the existence read's chunk size.
CONTACTABILITY_CHUNK = 250

CONTACTABILITY_SQL_TEMPLATE = (
    "SELECT b.borrower_id, b.marketing_eligible, b.consent_status, b.suppression_reason, "
    "b.dnc, b.eligibility_source, b.last_touch_at, b.eligible_recontact_at, "
    f"{eligible_sql_predicate('b')} AS contactable_sql "
    f"FROM {qualify('gold', 'borrower_dossier')} AS b "
    "WHERE 1 = 1 {borrower_clause}"
)


def _bool_or_none(value: Any) -> bool | None:
    if value is None or isinstance(value, bool):
        return value
    return str(value).strip().lower() in _TRUE_TEXT


class BorrowerContactabilityRow(BaseModel):
    """The seven gold transport columns plus the warehouse's own predicate verdict."""

    marketing_eligible: bool | None
    consent_status: str | None
    suppression_reason: str | None
    dnc: bool | None
    eligibility_source: str | None
    last_touch_at: datetime | None
    eligible_recontact_at: datetime | None
    contactable_sql: bool | None

    @field_validator("marketing_eligible", "dnc", "contactable_sql", mode="before")
    @classmethod
    def _warehouse_bool(cls, value: Any) -> bool | None:
        return _bool_or_none(value)

    @classmethod
    def from_gold(cls, row: dict[str, Any]) -> BorrowerContactabilityRow:
        """Every field passed explicitly, so ``model_fields_set`` holds all eight."""
        return cls(
            marketing_eligible=row.get("marketing_eligible"),
            consent_status=row.get("consent_status"),
            suppression_reason=row.get("suppression_reason"),
            dnc=row.get("dnc"),
            eligibility_source=row.get("eligibility_source"),
            last_touch_at=row.get("last_touch_at"),
            eligible_recontact_at=row.get("eligible_recontact_at"),
            contactable_sql=row.get("contactable_sql"),
        )


class BorrowerContactabilityMixin:
    """``contactability`` for ``DatabricksBorrowerRepository`` (its ``_client``)."""

    _client: Any

    def contactability(self, borrower_ids: Sequence[str]) -> dict[str, BorrowerContactabilityRow]:
        """Gold contactability by borrower id; ids absent from gold are absent here."""
        normalised = LeadCohortQuerySupport.normalise_borrower_ids(list(borrower_ids))
        found: dict[str, BorrowerContactabilityRow] = {}
        for start in range(0, len(normalised), CONTACTABILITY_CHUNK):
            chunk = normalised[start : start + CONTACTABILITY_CHUNK]
            params: dict[str, object] = {}
            clause = LeadCohortQuerySupport.in_clause(
                column="b.borrower_id",
                prefix="contactability_borrower_id",
                values=chunk,
                params=params,
            )
            rows = self._client.execute(
                CONTACTABILITY_SQL_TEMPLATE.format(borrower_clause=clause),
                params,
            )
            for row in rows or []:
                borrower_id = str(row.get("borrower_id") or "").strip()
                if borrower_id:
                    found[borrower_id] = BorrowerContactabilityRow.from_gold(row)
        return found
