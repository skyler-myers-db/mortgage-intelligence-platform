"""Access accountability for the audit ledger (D-audit-reads-c3).

Every successful cross-actor read of ``mip_app.action_audit`` -- the explorer
pages, the rollups, the facet and count reads, another actor's decision
receipt -- writes exactly ONE server-owned ``VIEW_AUDIT_LEDGER`` row, so the
ledger records who read it. The row says which surface was read, whether it
was a later page, how many rows came back, the SHA-256 of the filters and,
for a receipt, which row; it never carries ledger row contents, an actor
filter in clear, or an email.

The write is scheduled as a background task and is fail-open (the
``leads.py`` ``_safe_audit_write`` pattern): a Lakebase outage must not turn
a served ledger read into a 503, and the dropped write is observable as one
``audit.dropped`` event. A service, not a router helper, so routers never
import each other and later surfaces (refusal reports) reuse it.
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import BackgroundTasks

from backend.services.audit_metadata_policy import LEDGER_SURFACES
from backend.services.audit_store import AuditStore
from backend.services.observability import emit

log = logging.getLogger(__name__)

VIEW_AUDIT_LEDGER_EVENT_TYPE = "VIEW_AUDIT_LEDGER"
VIEW_AUDIT_LEDGER_ACTION = "view_audit_ledger"
AUDIT_LEDGER_ENTITY_TYPE = "audit_ledger"


def _write_ledger_read(store: AuditStore, **kwargs: Any) -> None:
    """Background writer: swallow and report every failure, never ``str(exc)``."""

    try:
        store.write(**kwargs)
    except Exception as exc:  # noqa: BLE001 -- background path must not raise
        emit(
            log,
            "audit.dropped",
            dependency="lakebase",
            exc_type=type(exc).__name__,
            event_type=VIEW_AUDIT_LEDGER_EVENT_TYPE,
            outcome="error",
        )


def record_ledger_read(
    background: BackgroundTasks,
    store: AuditStore,
    *,
    actor: str,
    surface: str,
    filter_fingerprint: str | None,
    has_cursor: bool,
    returned_row_count: int,
    read_audit_event_id: str | None = None,
) -> None:
    """Schedule the one ``VIEW_AUDIT_LEDGER`` row for a served ledger read."""

    if surface not in LEDGER_SURFACES:
        raise ValueError(f"unknown ledger surface: {surface!r}")
    payload: dict[str, Any] = {
        "ledger_surface": surface,
        "has_cursor": has_cursor,
        "returned_row_count": returned_row_count,
    }
    if filter_fingerprint is not None:
        payload["filter_fingerprint"] = filter_fingerprint
    if read_audit_event_id is not None:
        payload["read_audit_event_id"] = read_audit_event_id
    background.add_task(
        _write_ledger_read,
        store,
        actor=actor,
        action=VIEW_AUDIT_LEDGER_ACTION,
        event_type=VIEW_AUDIT_LEDGER_EVENT_TYPE,
        entity_type=AUDIT_LEDGER_ENTITY_TYPE,
        entity_id=read_audit_event_id or surface,
        payload_json=payload,
    )
