"""Reporting reads over sales lifecycle state: lifecycle, aging, standup,
conversion, campaign funnel, and outcome summary."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from datetime import UTC, datetime
from typing import Any

from backend.services.lakebase import LakebaseError
from backend.services.sales_state_core import _SalesStateCore
from backend.services.sales_state_mappers import (
    _cache_get,
    _cache_set,
    _public_competitor_label,
)


class _SalesStateReporting(_SalesStateCore):
    """Aggregate read paths that shape manager-facing reporting payloads."""

    def lifecycle_for(self, borrower_id: str, *, use_cache: bool = False) -> dict[str, Any]:
        cache_key = f"sales_state:lifecycle:{borrower_id}"
        if use_cache:
            cached = _cache_get(cache_key)
            if isinstance(cached, dict):
                return deepcopy(cached)
        row = (
            self._client.fetchone(
                """
            WITH latest_approval AS (
                SELECT approval_id, action, offer_code, decided_at
                FROM mip_app.approvals
                WHERE borrower_id = %(borrower_id)s
                ORDER BY decided_at DESC, approval_id::text DESC
                LIMIT 1
            ),
            latest_disposition AS (
                SELECT occurred_at AS disposition_at
                FROM mip_app.call_dispositions
                WHERE borrower_id = %(borrower_id)s
                ORDER BY occurred_at DESC, created_at DESC, disposition_id::text DESC
                LIMIT 1
            )
            SELECT
                CASE
                    WHEN a.action = 'approve' THEN 'approved'
                    WHEN a.action = 'reject' THEN 'rejected'
                    WHEN a.action = 'hold' THEN 'hold'
                    ELSE 'pending'
                END AS approval_status,
                CASE
                    WHEN d.disposition_at IS NOT NULL THEN 'actioned'
                    WHEN a.action = 'approve' THEN 'queued'
                    ELSE 'none'
                END AS outreach_status,
                CASE WHEN a.action = 'approve' THEN a.approval_id ELSE NULL END AS approval_id,
                CASE WHEN a.action = 'approve' THEN a.decided_at ELSE NULL END AS approved_at,
                d.disposition_at AS outreach_at,
                now() AS synced_at
            FROM latest_approval a
            FULL OUTER JOIN latest_disposition d ON true
            """,
                {"borrower_id": borrower_id},
            )
            or {}
        )
        result = {
            "borrower_id": borrower_id,
            "approval_status": row.get("approval_status") or "pending",
            "outreach_status": row.get("outreach_status") or "none",
            "approval_id": str(row["approval_id"]) if row.get("approval_id") else None,
            "approved_at": row.get("approved_at"),
            "outreach_at": row.get("outreach_at"),
            "synced_at": row.get("synced_at"),
            "assignment": self.active_assignment_for(borrower_id),
            "latest_disposition": self.latest_disposition_for(borrower_id),
        }
        if use_cache:
            _cache_set(cache_key, result)
        return deepcopy(result)

    def aging(self, *, older_than_days: int, limit: int = 50) -> list[dict[str, Any]]:
        rows = self._client.fetchall(
            """
            WITH latest_approval AS (
                SELECT DISTINCT ON (borrower_id)
                       borrower_id, action, decided_at
                FROM mip_app.approvals
                ORDER BY borrower_id, decided_at DESC, approval_id::text DESC
            ),
            latest_disposition AS (
                SELECT DISTINCT ON (borrower_id)
                       borrower_id, outcome, occurred_at
                FROM mip_app.call_dispositions
                ORDER BY borrower_id, occurred_at DESC, created_at DESC,
                         disposition_id::text DESC
            )
            SELECT a.borrower_id,
                   'approved' AS approval_status,
                   a.decided_at AS approved_at,
                   FLOOR(EXTRACT(EPOCH FROM (now() - a.decided_at)) / 86400)::int AS age_days,
                   CASE WHEN d.occurred_at IS NOT NULL THEN 'actioned' ELSE 'queued' END AS outreach_status,
                   d.occurred_at AS outreach_at,
                   la.assigned_to_email,
                   d.outcome AS latest_disposition_outcome,
                   d.occurred_at AS latest_disposition_at
            FROM latest_approval a
            LEFT JOIN mip_app.lead_assignments la
              ON la.borrower_id = a.borrower_id
             AND la.released_at IS NULL
            LEFT JOIN latest_disposition d ON d.borrower_id = a.borrower_id
            WHERE a.action = 'approve'
              AND d.occurred_at IS NULL
              AND a.decided_at <= now() - (%(older_than_days)s::int * interval '1 day')
            ORDER BY a.decided_at ASC
            LIMIT %(limit)s
            """,
            {"older_than_days": older_than_days, "limit": limit},
            limit=limit,
        )
        return rows

    def standup(self, *, date: str, visible_lo_emails: set[str] | None = None) -> dict[str, Any]:
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        rows = self._client.fetchall(
            f"""
            SELECT lo_email, outcome, COUNT(*) AS n
            FROM mip_app.call_dispositions
            WHERE occurred_at >= %(date)s::date
              AND occurred_at < (%(date)s::date + interval '1 day')
              {lo_filter}
            GROUP BY lo_email, outcome
            ORDER BY lo_email, outcome
            """,
            {"date": date, "lo_emails": sorted(visible_lo_emails or [])},
            limit=500,
        )
        by_lo: dict[str, Counter[str]] = {}
        totals: Counter[str] = Counter()
        for row in rows:
            lo_email = str(row.get("lo_email"))
            outcome = str(row.get("outcome"))
            n = int(row.get("n") or 0)
            by_lo.setdefault(lo_email, Counter())[outcome] += n
            totals[outcome] += n
        return {
            "date": date,
            "calls_logged": sum(totals.values()),
            "contacts_reached": totals["connected"]
            + totals["callback_scheduled"]
            + totals["application_started"],
            "callbacks_scheduled": totals["callback_scheduled"],
            "applications_started": totals["application_started"],
            "dead_leads": totals["dead"],
            "by_lo": [
                {"lo_email": lo, "outcomes": dict(counts), "calls_logged": sum(counts.values())}
                for lo, counts in sorted(by_lo.items())
            ],
        }

    def conversion(
        self,
        *,
        from_date: str,
        to_date: str,
        group_by: str,
        visible_lo_emails: set[str] | None = None,
    ) -> list[dict[str, Any]]:
        group_expr = "lo_email" if group_by == "lo" else "'all_cohorts'"
        lo_filter = "AND lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        rows = self._client.fetchall(
            f"""
            SELECT {group_expr} AS group_key,
                   COUNT(*) AS calls_attempted,
                   COUNT(DISTINCT borrower_id) AS unique_leads_contacted,
                   COUNT(DISTINCT borrower_id) FILTER (
                     WHERE outcome IN ('connected','callback_scheduled','application_started')
                   ) AS unique_contacts_reached,
                   COUNT(*) FILTER (WHERE outcome IN ('connected','callback_scheduled','application_started')) AS contacts_reached,
                   COUNT(*) FILTER (WHERE outcome = 'callback_scheduled') AS callbacks_scheduled,
                   COUNT(*) FILTER (WHERE outcome = 'application_started') AS applications_started,
                   COUNT(DISTINCT borrower_id) FILTER (WHERE outcome = 'application_started') AS unique_application_starts
            FROM mip_app.call_dispositions
            WHERE occurred_at >= %(from_date)s::date
              AND occurred_at < (%(to_date)s::date + interval '1 day')
              {lo_filter}
            GROUP BY 1
            ORDER BY applications_started DESC, calls_attempted DESC
            LIMIT 100
            """,
            {
                "from_date": from_date,
                "to_date": to_date,
                "lo_emails": sorted(visible_lo_emails or []),
            },
            limit=100,
        )
        out: list[dict[str, Any]] = []
        for row in rows:
            calls = int(row.get("calls_attempted") or 0)
            apps = int(row.get("applications_started") or 0)
            unique_leads = int(row.get("unique_leads_contacted") or 0)
            unique_contacts = int(row.get("unique_contacts_reached") or 0)
            unique_apps = int(row.get("unique_application_starts") or 0)
            out.append(
                {
                    "group_key": row.get("group_key"),
                    "calls_attempted": calls,
                    "contacts_reached": int(row.get("contacts_reached") or 0),
                    "callbacks_scheduled": int(row.get("callbacks_scheduled") or 0),
                    "applications_started": apps,
                    "unique_leads_contacted": unique_leads,
                    "unique_contacts_reached": unique_contacts,
                    "unique_application_starts": unique_apps,
                    "application_start_rate": (
                        round(unique_apps / unique_contacts, 4) if unique_contacts else 0.0
                    ),
                }
            )
        return out

    def campaign_performance_funnel(
        self,
        *,
        from_date: str,
        to_date: str,
        visible_lo_emails: set[str] | None = None,
    ) -> dict[str, int | datetime]:
        """Return a same-borrower, chronological observed funnel for benchmarks.

        Each stage uses the borrower's earliest event at or after the preceding
        stage. This prevents later funnel stages from being credited to an
        earlier outreach attempt and prevents independently counted populations
        from being multiplied together as one cohort. The result is a manager-
        visible team benchmark, not a claim that these borrowers belong to the
        Portfolio Builder selection.
        """

        disposition_scope = (
            "AND d.lo_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        )
        outcome_scope = (
            "AND o.assigned_to_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        )
        rows = self._client.fetchall(
            f"""
            /* campaign_performance_funnel: same-borrower nested sets */
            WITH attempted AS (
              SELECT d.borrower_id, MIN(d.occurred_at) AS attempted_at
              FROM mip_app.call_dispositions d
              WHERE d.occurred_at >= %(from_date)s::date
                AND d.occurred_at < (%(to_date)s::date + interval '1 day')
                {disposition_scope}
              GROUP BY d.borrower_id
            ),
            reached AS (
              SELECT d.borrower_id, MIN(d.occurred_at) AS reached_at
              FROM mip_app.call_dispositions d
              INNER JOIN attempted a ON a.borrower_id = d.borrower_id
              WHERE d.occurred_at >= a.attempted_at
                AND d.occurred_at < (%(to_date)s::date + interval '1 day')
                AND d.outcome IN ('connected','callback_scheduled','application_started')
                {disposition_scope}
              GROUP BY d.borrower_id
            ),
            started AS (
              SELECT d.borrower_id, MIN(d.occurred_at) AS started_at
              FROM mip_app.call_dispositions d
              INNER JOIN reached r ON r.borrower_id = d.borrower_id
              WHERE d.occurred_at >= r.reached_at
                AND d.occurred_at < (%(to_date)s::date + interval '1 day')
                AND d.outcome = 'application_started'
                {disposition_scope}
              GROUP BY d.borrower_id
            ),
            submitted AS (
              SELECT o.borrower_id, MIN(o.occurred_at) AS submitted_at
              FROM mip_app.lead_outcomes o
              INNER JOIN started s ON s.borrower_id = o.borrower_id
              WHERE o.occurred_at >= s.started_at
                AND o.occurred_at < (%(to_date)s::date + interval '1 day')
                AND o.outcome_type = 'application_submitted'
                {outcome_scope}
              GROUP BY o.borrower_id
            ),
            funded AS (
              SELECT o.borrower_id, MIN(o.occurred_at) AS funded_at
              FROM mip_app.lead_outcomes o
              INNER JOIN submitted s ON s.borrower_id = o.borrower_id
              WHERE o.occurred_at >= s.submitted_at
                AND o.occurred_at < (%(to_date)s::date + interval '1 day')
                AND o.outcome_type = 'closed_funded'
                {outcome_scope}
              GROUP BY o.borrower_id
            )
            SELECT
              statement_timestamp() AS snapshot_at,
              (SELECT COUNT(*) FROM attempted) AS unique_leads_attempted,
              (SELECT COUNT(*) FROM reached) AS unique_contacts_reached,
              (SELECT COUNT(*) FROM started) AS unique_application_starts,
              (SELECT COUNT(*) FROM submitted) AS unique_applications_submitted,
              (SELECT COUNT(*) FROM funded) AS unique_closed_funded
            """,
            {
                "from_date": from_date,
                "to_date": to_date,
                "lo_emails": sorted(visible_lo_emails or []),
            },
            limit=1,
        )
        row = rows[0] if rows else {}
        snapshot_value = row.get("snapshot_at")
        if isinstance(snapshot_value, datetime):
            snapshot_at = snapshot_value
        elif snapshot_value:
            try:
                snapshot_at = datetime.fromisoformat(str(snapshot_value).replace("Z", "+00:00"))
            except ValueError as exc:
                raise LakebaseError("campaign performance snapshot timestamp is invalid") from exc
        else:
            raise LakebaseError("campaign performance snapshot timestamp is missing")
        if snapshot_at.tzinfo is None or snapshot_at.utcoffset() is None:
            raise LakebaseError("campaign performance snapshot timestamp is timezone-naive")
        return {
            "snapshot_at": snapshot_at.astimezone(UTC),
            "unique_leads_attempted": int(row.get("unique_leads_attempted") or 0),
            "unique_contacts_reached": int(row.get("unique_contacts_reached") or 0),
            "unique_application_starts": int(row.get("unique_application_starts") or 0),
            "unique_applications_submitted": int(row.get("unique_applications_submitted") or 0),
            "unique_closed_funded": int(row.get("unique_closed_funded") or 0),
        }

    def outcome_summary(
        self,
        *,
        from_date: str,
        to_date: str,
        visible_lo_emails: set[str] | None = None,
    ) -> dict[str, Any]:
        lo_filter = (
            "AND assigned_to_email = ANY(%(lo_emails)s)" if visible_lo_emails is not None else ""
        )
        params = {
            "from_date": from_date,
            "to_date": to_date,
            "lo_emails": sorted(visible_lo_emails or []),
        }
        rows = self._client.fetchall(
            f"""
            SELECT outcome_type, source_system, assigned_to_email,
                   COALESCE(competitor_lender_label, '') AS competitor_lender_label,
                   COUNT(*) AS n
            FROM mip_app.lead_outcomes
            WHERE occurred_at >= %(from_date)s::date
              AND occurred_at < (%(to_date)s::date + interval '1 day')
              {lo_filter}
            GROUP BY outcome_type, source_system, assigned_to_email, competitor_lender_label
            ORDER BY n DESC
            LIMIT 1000
            """,
            params,
            limit=1000,
        )
        unique_rows = self._client.fetchall(
            f"""
            SELECT
              COUNT(DISTINCT borrower_id) FILTER (
                WHERE outcome_type = 'application_submitted'
              ) AS unique_applications_submitted,
              COUNT(DISTINCT borrower_id) FILTER (
                WHERE outcome_type = 'closed_funded'
              ) AS unique_closed_funded
            FROM mip_app.lead_outcomes
            WHERE occurred_at >= %(from_date)s::date
              AND occurred_at < (%(to_date)s::date + interval '1 day')
              {lo_filter}
            """,
            params,
            limit=1,
        )
        unique_row = unique_rows[0] if unique_rows else {}
        totals: Counter[str] = Counter()
        by_source: dict[str, Counter[str]] = {}
        by_lo: dict[str, Counter[str]] = {}
        competitors: Counter[str] = Counter()
        for row in rows:
            outcome_type = str(row.get("outcome_type") or "")
            source_system = str(row.get("source_system") or "unknown")
            lo_email = str(row.get("assigned_to_email") or "unassigned")
            competitor = _public_competitor_label(row.get("competitor_lender_label"))
            n = int(row.get("n") or 0)
            totals[outcome_type] += n
            by_source.setdefault(source_system, Counter())[outcome_type] += n
            by_lo.setdefault(lo_email, Counter())[outcome_type] += n
            if outcome_type == "lost_to_competitor" and competitor:
                competitors[competitor] += n
        source_statuses = self.outcome_source_statuses()
        return {
            "from_date": from_date,
            "to_date": to_date,
            "total_outcomes": sum(totals.values()),
            "applications_submitted": totals["application_submitted"],
            "closed_funded": totals["closed_funded"],
            "unique_applications_submitted": int(
                unique_row.get("unique_applications_submitted") or 0
            ),
            "unique_closed_funded": int(unique_row.get("unique_closed_funded") or 0),
            "lost_to_competitor": totals["lost_to_competitor"],
            "withdrawn": totals["withdrawn"],
            "not_qualified": totals["not_qualified"],
            "by_source_system": [
                {"source_system": key, "outcomes": dict(counts), "total": sum(counts.values())}
                for key, counts in sorted(by_source.items())
            ],
            "source_statuses": [
                {
                    **status,
                    "outcome_count": sum(
                        by_source.get(str(status["source_system"]), Counter()).values()
                    ),
                }
                for status in source_statuses
            ],
            "by_lo": [
                {"lo_email": key, "outcomes": dict(counts), "total": sum(counts.values())}
                for key, counts in sorted(by_lo.items())
            ],
            "top_competitors": [
                {"competitor_lender_label": key, "lost_to_competitor": count}
                for key, count in competitors.most_common(5)
            ],
        }
