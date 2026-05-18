"""Governed Sales Ops answers for narrow Genie prompts backed by Lakebase."""
from __future__ import annotations

import hashlib
import re
import time
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

from fastapi import HTTPException

from backend.services.databricks_sql_helpers import qualify
from backend.services.genie_answers import GenieMessageResponse, GenieProof
from backend.services.lakebase import LakebaseClient
from backend.services.repositories import BorrowerRepository
from backend.services.sales_state import SalesStateStore


def _sales_ops_question_kind(question: str) -> str | None:
    """Detect Sales Manager operational prompts that live in Lakebase state."""

    q = question.lower()
    if re.search(r"\b(lo|loan officer)\b", q) and re.search(
        r"application[-\s]?start|app(?:lication)? start|conversion", q
    ):
        return "conversion"
    if "approved" in q and re.search(r"application[-\s]?started|application started", q):
        return "approval_to_application"
    if re.search(r"\b(lo|loan officer)\b", q) and re.search(r"\bcalls?\b|called|standup", q):
        return "standup"
    if re.search(r"approved", q) and re.search(r"untouched|not touched|stale|aging|older than|7 days", q):
        return "aging"
    if re.search(r"\b(lo|loan officer)\b", q) and re.search(r"\bqueue\b|call[-\s]?list|borrowers", q):
        return "queue"
    return None


def sales_ops_genie_response(
    lakebase: LakebaseClient,
    borrower_repo: BorrowerRepository,
    *,
    actor: str,
    question: str,
    conversation_id: str | None,
) -> GenieMessageResponse | None:
    kind = _sales_ops_question_kind(question)
    if kind is None:
        return None
    try:
        sales_store = SalesStateStore(lakebase)
        sales_store.require_manager_actor(actor)
        visible_lo_emails = sales_store.visible_lo_emails(actor=actor)
    except (KeyError, PermissionError) as exc:
        raise HTTPException(status_code=403, detail="Sales Ops Genie questions require sales-manager access") from exc
    started = time.monotonic()
    question_hash = hashlib.sha256(question.encode("utf-8")).hexdigest()[:16]
    message_id = f"sales-ops-{uuid4()}"
    source_assets: list[str]
    sql_query: str
    rows: list[dict[str, Any]]
    answer: str
    if kind == "conversion":
        week_start = datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).date().weekday())
        source_assets = ["mip_app.call_dispositions"]
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = f"""
SELECT lo_email AS group_key,
       COUNT(*) AS calls_attempted,
       COUNT(*) FILTER (WHERE outcome IN ('connected','callback_scheduled','application_started')) AS contacts_reached,
       COUNT(*) FILTER (WHERE outcome = 'callback_scheduled') AS callbacks_scheduled,
       COUNT(*) FILTER (WHERE outcome = 'application_started') AS applications_started
FROM mip_app.call_dispositions
WHERE occurred_at >= %(week_start)s::date
  {lo_filter}
GROUP BY lo_email
ORDER BY CASE WHEN COUNT(*) = 0 THEN 0
              ELSE COUNT(*) FILTER (WHERE outcome = 'application_started')::float / COUNT(*)
         END DESC,
         applications_started DESC,
         calls_attempted DESC
LIMIT 10
"""
        raw_rows = lakebase.fetchall(
            sql_query,
            {"week_start": week_start.isoformat(), "lo_emails": sorted(visible_lo_emails or [])},
            limit=10,
        )
        rows = []
        for row in raw_rows:
            calls = int(row.get("calls_attempted") or 0)
            apps = int(row.get("applications_started") or 0)
            rows.append(
                {
                    "lo_email": row.get("group_key"),
                    "calls_attempted": calls,
                    "contacts_reached": int(row.get("contacts_reached") or 0),
                    "callbacks_scheduled": int(row.get("callbacks_scheduled") or 0),
                    "applications_started": apps,
                    "application_start_rate": round(apps / calls, 4) if calls else 0.0,
                }
            )
        if rows:
            top = rows[0]
            answer = (
                f"{top['lo_email']} has the highest application-start rate this week at "
                f"{top['application_start_rate'] * 100:.1f}% "
                f"({top['applications_started']} applications from {top['calls_attempted']} logged calls). "
                "This answer is routed through governed Sales Ops state because LO dispositions live in Lakebase."
            )
        else:
            answer = (
                "No loan-officer dispositions have been logged this week, so there is no application-start "
                "rate to rank yet. Source: governed Sales Ops state in Lakebase."
            )
    elif kind == "approval_to_application":
        week_start = datetime.now(UTC).date() - timedelta(days=datetime.now(UTC).date().weekday())
        source_assets = ["mip_app.call_dispositions"]
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = f"""
SELECT COUNT(*) FILTER (WHERE outcome = 'application_started') AS applications_started,
       COUNT(*) AS calls_attempted
FROM mip_app.call_dispositions
WHERE occurred_at >= %(week_start)s::date
  {lo_filter}
"""
        raw_rows = lakebase.fetchall(
            sql_query,
            {"week_start": week_start.isoformat(), "lo_emails": sorted(visible_lo_emails or [])},
            limit=1,
        )
        first = raw_rows[0] if raw_rows else {}
        apps = int(first.get("applications_started") or 0)
        calls = int(first.get("calls_attempted") or 0)
        rows = [{"applications_started": apps, "calls_attempted": calls}]
        answer = (
            f"{apps} approved leads have progressed to application started this week "
            f"across {calls} logged LO dispositions. Source: governed Sales Ops state in Lakebase."
        )
    elif kind == "standup":
        yesterday = (datetime.now(UTC).date() - timedelta(days=1)).isoformat()
        source_assets = ["mip_app.call_dispositions"]
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = f"""
SELECT lo_email, outcome, COUNT(*) AS n
FROM mip_app.call_dispositions
WHERE occurred_at >= %(day)s::date
  AND occurred_at < (%(day)s::date + interval '1 day')
  {lo_filter}
GROUP BY lo_email, outcome
ORDER BY lo_email, outcome
"""
        raw_rows = lakebase.fetchall(
            sql_query,
            {"day": yesterday, "lo_emails": sorted(visible_lo_emails or [])},
            limit=500,
        )
        rows = [
            {"lo_email": row.get("lo_email"), "outcome": row.get("outcome"), "count": int(row.get("n") or 0)}
            for row in raw_rows
        ]
        calls = sum(int(row["count"]) for row in rows)
        apps = sum(int(row["count"]) for row in rows if row.get("outcome") == "application_started")
        callbacks = sum(int(row["count"]) for row in rows if row.get("outcome") == "callback_scheduled")
        answer = (
            f"Yesterday's LO standup has {calls} logged calls, {callbacks} callbacks scheduled, "
            f"and {apps} applications started. Source: governed Sales Ops state in Lakebase."
        )
    elif kind == "queue":
        borrower_asset = qualify("gold", "borrower_360")
        source_assets = ["mip_app.lead_assignments", borrower_asset]
        lo_filter = "AND assigned_to_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        sql_query = f"""
SELECT borrower_id, assigned_to_email, assigned_at
FROM mip_app.lead_assignments
WHERE released_at IS NULL
  {lo_filter}
ORDER BY assigned_at ASC
LIMIT 10
"""
        rows = [
            {
                "borrower_id": row.get("borrower_id"),
                "assigned_to_email": row.get("assigned_to_email"),
                "assigned_at": str(row.get("assigned_at") or ""),
            }
            for row in lakebase.fetchall(sql_query, {"lo_emails": sorted(visible_lo_emails or [])}, limit=10)
        ]
        answer = (
            "Open Lead Queue with `assigned_to=<lo email>&approval_status=approved&outreach_status=queued` "
            "to rank an LO's current queue by aging, score, and equity. The backend keeps assignment state "
            f"in Lakebase and borrower rank in `{borrower_asset}`."
        )
    else:
        source_assets = ["mip_app.approvals", "mip_app.lead_assignments", "mip_app.call_dispositions"]
        lo_filter = (
            "AND (la.assigned_to_email IS NULL OR la.assigned_to_email = ANY(%(lo_emails)s))"
            if visible_lo_emails is not None
            else ""
        )
        sql_query = f"""
WITH latest_approval AS (
  SELECT DISTINCT ON (borrower_id) borrower_id, action, decided_at
  FROM mip_app.approvals
  ORDER BY borrower_id, decided_at DESC
),
latest_disposition AS (
  SELECT DISTINCT ON (borrower_id) borrower_id, occurred_at
  FROM mip_app.call_dispositions
  ORDER BY borrower_id, occurred_at DESC, created_at DESC
)
SELECT a.borrower_id,
       FLOOR(EXTRACT(EPOCH FROM (now() - a.decided_at)) / 86400)::int AS age_days
FROM latest_approval a
LEFT JOIN latest_disposition d ON d.borrower_id = a.borrower_id
LEFT JOIN mip_app.lead_assignments la
  ON la.borrower_id = a.borrower_id
 AND la.released_at IS NULL
WHERE a.action = 'approve'
  AND d.occurred_at IS NULL
  {lo_filter}
  AND a.decided_at <= now() - interval '7 days'
ORDER BY a.decided_at ASC
LIMIT 100
"""
        rows = []
        for row in lakebase.fetchall(sql_query, {"lo_emails": sorted(visible_lo_emails or [])}, limit=100):
            borrower_id = str(row.get("borrower_id") or "")
            if not borrower_id or borrower_repo.get(borrower_id) is None:
                continue
            rows.append({"borrower_id": borrower_id, "age_days": int(row.get("age_days") or 0)})
            if len(rows) >= 10:
                break
        answer = (
            f"{len(rows)} approved leads are older than 7 days with no outreach in the returned triage window. "
            "Open Lead Queue with `approval_status=approved&outreach_status=queued&aged_days=7` for the full working list. "
            "Source: governed Sales Ops state in Lakebase."
        )
    elapsed_ms = int((time.monotonic() - started) * 1000)
    return GenieMessageResponse(
        conversation_id=conversation_id or "",
        question=question,
        answer=answer,
        source="sales_ops",
        trusted_assets=source_assets,
        message_id=message_id,
        elapsed_ms=elapsed_ms,
        question_hash=question_hash,
        sql_query=sql_query.strip(),
        row_count=len(rows),
        proof=GenieProof(
            sql_query=sql_query.strip(),
            source_assets=source_assets,
            row_count=len(rows),
            trusted=True,
            filters=[f"sales_ops_kind = {kind}"],
            conversation_id=conversation_id,
            message_id=message_id,
            elapsed_ms=elapsed_ms,
            generated_at=datetime.now(UTC).isoformat(),
        ),
        table_rows=rows,
        follow_up_questions=[
            "Show approved leads that have not been touched in 7 days.",
            "How many calls did each LO make yesterday?",
        ],
    )
