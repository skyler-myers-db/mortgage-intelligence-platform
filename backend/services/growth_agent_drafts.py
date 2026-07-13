"""Draft-only notification helpers for saved Growth Agent watchlists."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Sequence
from typing import Any

import psycopg
from fastapi import HTTPException

from backend.schemas.growth_agent import GrowthAgentMonitor, GrowthAgentNotificationDraft
from backend.services.audit_lakebase_store import write_audit_event_in_transaction
from backend.services.error_sanitizer import safe_dependency_detail
from backend.services.growth_agent_ledger_sql import (
    NOTIFICATION_DRAFT_ATTACH_AUDIT_SQL,
    NOTIFICATION_DRAFT_INSERT_SQL,
    NOTIFICATION_DRAFT_SELECT_ACTIVE_SQL,
    NOTIFICATION_DRAFT_SELECT_BY_REQUEST_ID_SQL,
)
from backend.services.growth_agent_notification_intelligence import (
    NotificationIntelligence,
    recommend_notification_intelligence,
)
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
    intents = {
        channel: _notification_draft_intent(
            actor=actor,
            monitor=monitor,
            run_id=run_id,
            route=route,
            actionable_total=actionable_total,
            channel=channel,
        )
        for channel in channels
    }
    request_ids = {
        channel: _draft_request_id(
            request_id,
            monitor_id=monitor.monitor_id,
            run_id=run_id,
            channel=channel,
        )
        for channel in channels
    }
    try:
        with lakebase.transaction() as conn:
            replayed = [
                _find_existing_draft(
                    conn,
                    actor=actor,
                    monitor_id=monitor.monitor_id,
                    run_id=run_id,
                    channel=channel,
                    request_id=request_ids[channel],
                    expected_intent=intents[channel],
                )
                for channel in channels
            ]
        if all(row is not None for row in replayed):
            return [_notification_draft_from_row(row) for row in replayed if row is not None]

        intelligence = recommend_notification_intelligence(
            monitor_name=monitor.name,
            workflow_id=monitor.workflow_id,
        )
        drafts: list[GrowthAgentNotificationDraft] = []
        with lakebase.transaction() as conn:
            for channel in channels:
                params = {
                    "actor_email": actor,
                    "monitor_id": monitor.monitor_id,
                    "run_id": run_id,
                    "channel": channel,
                    "title": _notification_draft_title(
                        monitor,
                        channel=channel,
                        actionable_total=actionable_total,
                    ),
                    "body": _notification_draft_body(
                        monitor,
                        channel=channel,
                        route=route,
                        actionable_total=actionable_total,
                        intelligence=intelligence,
                    ),
                    "generation_mode": intelligence.generation_mode,
                    "generator_label": intelligence.generator_label,
                    "strategy_summary": intelligence.strategy_summary,
                    "request_id": request_ids[channel],
                    "intent_payload": intents[channel],
                    "intent_hash": _intent_hash(intents[channel]),
                }
                draft_row = _find_existing_draft(
                    conn,
                    actor=actor,
                    monitor_id=monitor.monitor_id,
                    run_id=run_id,
                    channel=channel,
                    request_id=request_ids[channel],
                    expected_intent=intents[channel],
                )
                if draft_row is not None:
                    drafts.append(_notification_draft_from_row(draft_row))
                    continue
                draft_row = _txn_fetchone(conn, NOTIFICATION_DRAFT_INSERT_SQL, params)
                if draft_row is None:
                    draft_row = _find_existing_draft(
                        conn,
                        actor=actor,
                        monitor_id=monitor.monitor_id,
                        run_id=run_id,
                        channel=channel,
                        request_id=request_ids[channel],
                        expected_intent=intents[channel],
                    )
                    if draft_row is None:
                        raise HTTPException(
                            status_code=409,
                            detail="notification draft conflicts with an existing immutable draft",
                        )
                    drafts.append(_notification_draft_from_row(draft_row))
                    continue
                event = write_audit_event_in_transaction(
                    conn,
                    actor=actor,
                    action="growth_agent.notification_draft",
                    entity_type="growth_agent_notification_draft",
                    entity_id=str(draft_row["draft_id"]),
                    payload_json={
                        "workflow_id": monitor.workflow_id,
                        "run_id": run_id,
                        "channel": channel,
                        "generation_mode": intelligence.generation_mode,
                        "actionable_total": actionable_total,
                        "route": route,
                        "source_assets": monitor.source_assets,
                    },
                    event_type="GROWTH_AGENT_NOTIFICATION_DRAFT",
                )
                attached = _txn_fetchone(
                    conn,
                    NOTIFICATION_DRAFT_ATTACH_AUDIT_SQL,
                    {"draft_id": draft_row["draft_id"], "audit_event_id": event.event_id},
                )
                if attached is None:
                    raise LakebaseError("notification draft audit link could not be persisted")
                drafts.append(_notification_draft_from_row(attached))
    except HTTPException:
        raise
    except (LakebaseError, psycopg.Error) as exc:
        raise HTTPException(status_code=503, detail=safe_dependency_detail("lakebase")) from exc
    return drafts


def _notification_draft_intent(
    *,
    actor: str,
    monitor: GrowthAgentMonitor,
    run_id: str,
    route: str,
    actionable_total: int,
    channel: str,
) -> str:
    payload = {
        "actor": actor,
        "monitor_id": monitor.monitor_id,
        "monitor_name": monitor.name,
        "workflow_id": monitor.workflow_id,
        "run_id": run_id,
        "route": route,
        "actionable_total": actionable_total,
        "channel": channel,
        "source_assets": monitor.source_assets,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def _intent_hash(intent_payload: str) -> str:
    return hashlib.sha256(intent_payload.encode("utf-8")).hexdigest()


def _find_existing_draft(
    conn: Any,
    *,
    actor: str,
    monitor_id: str,
    run_id: str,
    channel: str,
    request_id: str | None,
    expected_intent: str,
) -> dict[str, Any] | None:
    params = {
        "actor_email": actor,
        "monitor_id": monitor_id,
        "run_id": run_id,
        "channel": channel,
        "request_id": request_id,
    }
    row = (
        _txn_fetchone(conn, NOTIFICATION_DRAFT_SELECT_BY_REQUEST_ID_SQL, params)
        if request_id is not None
        else _txn_fetchone(conn, NOTIFICATION_DRAFT_SELECT_ACTIVE_SQL, params)
    )
    if row is None:
        return None
    stored_intent = str(row.get("intent_payload") or "")
    stored_hash = str(row.get("intent_hash") or "")
    if stored_intent != expected_intent or stored_hash != _intent_hash(expected_intent):
        raise HTTPException(
            status_code=409,
            detail="request_id already belongs to a different notification draft intent",
        )
    if not row.get("audit_event_id"):
        raise HTTPException(
            status_code=409,
            detail="notification draft replay is missing its durable audit link",
        )
    return row


def _notification_draft_title(
    monitor: GrowthAgentMonitor,
    *,
    channel: str,
    actionable_total: int,
) -> str:
    if channel == "slack":
        return f"{monitor.name}: {actionable_total:,} eligible"
    return f"Operations brief: {monitor.name}"


def _notification_draft_body(
    monitor: GrowthAgentMonitor,
    *,
    channel: str,
    route: str,
    actionable_total: int,
    intelligence: NotificationIntelligence,
) -> str:
    borrower_label = "borrower" if actionable_total == 1 else "borrowers"
    if channel == "slack":
        return (
            f"{actionable_total:,} eligible {borrower_label} in {monitor.name}. "
            f"{intelligence.slack_context}. "
            f"Review: {route}"
        )
    return (
        "Operations brief\n"
        f"Watchlist: {monitor.name}\n"
        f"Summary: {intelligence.teams_summary}.\n"
        f"Eligible population: {actionable_total:,} {borrower_label}\n"
        f"Operator action: {intelligence.operator_action}.\n"
        f"MIP route: {route}"
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
        generation_mode=row.get("generation_mode") or "governed_fallback",
        generator_label=row.get("generator_label") or "Governed notification framework",
        strategy_summary=row.get("strategy_summary") or "Reviewed internal notification framing.",
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
