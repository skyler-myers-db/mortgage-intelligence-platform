"""Draft-only notification helpers for saved Growth Agent watchlists."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import psycopg
from fastapi import HTTPException

from backend.schemas.growth_agent import GrowthAgentMonitor, GrowthAgentNotificationDraft
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import NOTIFICATION_DRAFT_UPSERT_SQL
from backend.services.lakebase import LakebaseClient, LakebaseError


def create_notification_drafts(
    lakebase: LakebaseClient,
    *,
    actor: str,
    monitor: GrowthAgentMonitor,
    run_id: str,
    route: str,
    actionable_total: int,
    channels: Sequence[str],
    request_id: str | None = None,
) -> list[GrowthAgentNotificationDraft]:
    drafts: list[GrowthAgentNotificationDraft] = []
    try:
        with lakebase.transaction() as conn:
            for channel in channels:
                draft_row = _txn_fetchone(
                    conn,
                    NOTIFICATION_DRAFT_UPSERT_SQL,
                    {
                        "actor_email": actor,
                        "monitor_id": monitor.monitor_id,
                        "run_id": run_id,
                        "channel": channel,
                        "title": _notification_draft_title(monitor, actionable_total),
                        "body": _notification_draft_body(
                            monitor,
                            channel=channel,
                            route=route,
                            actionable_total=actionable_total,
                        ),
                        "request_id": _draft_request_id(
                            request_id,
                            monitor_id=monitor.monitor_id,
                            run_id=run_id,
                            channel=channel,
                        ),
                    },
                )
                if draft_row is None:
                    raise HTTPException(status_code=409, detail="notification draft could not be written")
                write_audit_event_in_transaction(
                    conn,
                    actor=actor,
                    action="growth_agent.notification_draft",
                    entity_type="growth_agent_notification_draft",
                    entity_id=str(draft_row["draft_id"]),
                    payload_json={
                        "workflow_id": monitor.workflow_id,
                        "run_id": run_id,
                        "channel": channel,
                        "actionable_total": actionable_total,
                        "route": route,
                        "source_assets": monitor.source_assets,
                    },
                    event_type="GROWTH_AGENT_NOTIFICATION_DRAFT",
                )
                drafts.append(_notification_draft_from_row(draft_row))
    except HTTPException:
        raise
    except (LakebaseError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return drafts


def _notification_draft_title(monitor: GrowthAgentMonitor, actionable_total: int) -> str:
    return f"{monitor.name} - {actionable_total:,} eligible"


def _notification_draft_body(
    monitor: GrowthAgentMonitor,
    *,
    channel: str,
    route: str,
    actionable_total: int,
) -> str:
    destination = "Slack" if channel == "slack" else "Microsoft Teams"
    return (
        f"Draft for {destination}: {monitor.name} refreshed with "
        f"{actionable_total:,} eligible borrowers. Review the current watchlist in MIP: {route}. "
        "No borrower identities, contact data, or outbound messages are included."
    )


def _draft_request_id(
    request_id: str | None,
    *,
    monitor_id: str,
    run_id: str,
    channel: str,
) -> str | None:
    if request_id is None:
        return None
    return f"{request_id}-{monitor_id}-{run_id}-{channel}"


def _notification_draft_from_row(row: dict[str, Any]) -> GrowthAgentNotificationDraft:
    return GrowthAgentNotificationDraft(
        draft_id=str(row["draft_id"]),
        monitor_id=str(row["monitor_id"]),
        run_id=str(row["run_id"]),
        channel=row["channel"],
        title=str(row["title"]),
        body=str(row["body"]),
        status=row.get("status") or "draft",
        created_at=row.get("created_at"),
        updated_at=row.get("updated_at"),
    )


def _txn_fetchone(conn: Any, sql: str, params: dict[str, Any]) -> dict[str, Any] | None:
    execute = getattr(conn, "execute", None)
    if callable(execute):
        row = execute(sql, params).fetchone()
        return dict(row) if row is not None else None
    with conn.cursor() as cur:
        cur.execute(sql, params)
        row = cur.fetchone()
        return dict(row) if row is not None else None
