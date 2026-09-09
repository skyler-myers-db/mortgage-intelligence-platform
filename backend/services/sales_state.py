"""Lakebase-backed sales lifecycle state helpers with short-lived caches."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated, Any
from uuid import uuid4

from fastapi import Depends

from backend.schemas.lead import LeadSummary
from backend.services.lakebase import LakebaseClient, get_lakebase_client
from backend.services.lakebase_bootstrap import ensure_loan_officer_lifecycle_schema
from backend.services.sales_state_mappers import (
    _CONFIGURED_OUTCOME_SOURCE_STATUSES,
    _OUTCOME_SOURCE_LABELS,
    _OUTCOME_SOURCE_STATUS_BY_TYPE,
    _OUTCOME_SOURCE_STATUS_LIST,
    _OUTCOME_SOURCE_SYSTEMS,
    _PUBLIC_COMPETITOR_LABEL_RE,
    _SALES_AUDIT_INSERT_SQL,
    _SALES_STATE_CACHE,
    _assignment_duration_matches,
    _assignment_from_row,
    _cache_get,
    _cache_set,
    _copy_assignment,
    _copy_disposition,
    _copy_member,
    _datetime_utc,
    _datetimes_equal,
    _disposition_from_row,
    _iso_or_none,
    _outcome_from_row,
    _public_competitor_label,
    _sales_state_ttl_s,
    clear_sales_state_cache,
    get_sales_lakebase,
)
from backend.services.sales_state_reporting import _SalesStateReporting
from backend.services.sales_state_writes import _SalesStateWrites

# Import hub: the split moved module state, row mappers, and the store's read /
# write / reporting method groups into sibling modules. Every name callers and
# tests resolve through ``backend.services.sales_state`` stays importable here.
__all__ = [
    "LakebaseDep",
    "SalesStateStore",
    "_CONFIGURED_OUTCOME_SOURCE_STATUSES",
    "_OUTCOME_SOURCE_LABELS",
    "_OUTCOME_SOURCE_STATUS_BY_TYPE",
    "_OUTCOME_SOURCE_STATUS_LIST",
    "_OUTCOME_SOURCE_SYSTEMS",
    "_PUBLIC_COMPETITOR_LABEL_RE",
    "_SALES_AUDIT_INSERT_SQL",
    "_SALES_STATE_CACHE",
    "_assignment_duration_matches",
    "_assignment_from_row",
    "_cache_get",
    "_cache_set",
    "_copy_assignment",
    "_copy_disposition",
    "_copy_member",
    "_datetime_utc",
    "_datetimes_equal",
    "_disposition_from_row",
    "_iso_or_none",
    "_outcome_from_row",
    "_public_competitor_label",
    "_sales_state_ttl_s",
    "clear_sales_state_cache",
    "get_sales_lakebase",
    "get_sales_state_store",
    "hydrate_leads_with_sales_state",
    "new_server_request_id",
]

LakebaseDep = Annotated[LakebaseClient, Depends(get_lakebase_client)]


def get_sales_state_store(client: LakebaseDep) -> SalesStateStore:
    return SalesStateStore(client)


class SalesStateStore(_SalesStateWrites, _SalesStateReporting):
    """Lakebase-backed sales work-management state.

    UC gold remains the source for borrower facts and scoring. Lakebase is the
    authoritative app-state ledger for manager assignment and LO disposition
    actions, because those are human workflow events rather than Cotality facts.
    """


def hydrate_leads_with_sales_state(
    leads: list[LeadSummary],
    store: SalesStateStore | None = None,
    *,
    actor: str | None = None,
) -> list[LeadSummary]:
    if not leads:
        return leads
    store = store or SalesStateStore()
    # S2 backstop: assignment queries read the lifecycle ``status`` column,
    # which the deploy-time migrate job owns; this per-process memoized
    # bootstrap covers instances running new code before the next migrate.
    ensure_loan_officer_lifecycle_schema(store._client)
    visible_lo_emails: set[str] | None
    if actor is None:
        visible_lo_emails = set()
    else:
        try:
            visible_lo_emails = store.visible_lo_emails(actor=actor)
        except KeyError:
            visible_lo_emails = set()
    if visible_lo_emails == set():
        hydrated: list[LeadSummary] = []
        for lead in leads:
            update: dict[str, Any] = {}
            if lead.approved_at is not None:
                age = datetime.now(tz=lead.approved_at.tzinfo) - lead.approved_at
                update["aging_days"] = max(0, age.days)
            hydrated.append(lead.model_copy(update=update))
        return hydrated
    borrower_ids = [lead.borrower_id for lead in leads]
    assignments = store.assignments_for(borrower_ids)
    dispositions = store.latest_dispositions_for(borrower_ids)
    hydrated: list[LeadSummary] = []
    for lead in leads:
        update: dict[str, Any] = {}
        assignment = assignments.get(lead.borrower_id)
        if assignment is not None and (
            visible_lo_emails is None or assignment.assigned_to_email in visible_lo_emails
        ):
            update.update(
                {
                    "assigned_to_email": assignment.assigned_to_email,
                    "assigned_to_label": assignment.assigned_to_label,
                    "assigned_at": assignment.assigned_at,
                    "assignment_expires_at": assignment.expires_at,
                    "assignment_status": assignment.status,
                    "assignment_id": assignment.assignment_id,
                }
            )
        disposition = dispositions.get(lead.borrower_id)
        if disposition is not None and (
            visible_lo_emails is None or disposition.lo_email in visible_lo_emails
        ):
            update.update(
                {
                    "latest_disposition_outcome": disposition.outcome,
                    "latest_disposition_at": disposition.occurred_at,
                    "latest_callback_at": disposition.callback_at,
                }
            )
        if lead.approved_at is not None:
            age = datetime.now(tz=lead.approved_at.tzinfo) - lead.approved_at
            update["aging_days"] = max(0, age.days)
        hydrated.append(lead.model_copy(update=update))
    return hydrated


def new_server_request_id(prefix: str) -> str:
    return f"{prefix}-{uuid4()}"
