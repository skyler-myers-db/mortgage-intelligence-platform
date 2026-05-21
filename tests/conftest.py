"""Shared pytest fixtures for the Module 0 backend.

Slice-4 invariant: the production FastAPI app wires every repository to
the live Databricks SQL warehouse. Unit tests must never open a
warehouse connection; we achieve that by overriding each factory via
``app.dependency_overrides`` with the synthetic in-process
implementations in ``tests/fixtures/in_process_repos.py``.

Slice 5 extends the override set to cover the audit store and the
Lakebase client so no test opens a Postgres connection either. The
audit surface is served by an ``InMemoryAuditStore`` (fast, unit-test
friendly) and the Lakebase client is a minimal fake that records
execute / fetchone / fetchall calls for assertion.

The override is installed by an autouse session fixture so every unit-
test module automatically sees the stubbed routers. Tests that want to
swap in a custom stub for a single route can layer their own override
on top and clear it in teardown -- dependency_overrides is a plain
dict.
"""
from __future__ import annotations

# ruff: noqa: E402,I001

from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from typing import Any
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from backend.config.settings import settings

# Unit tests assert the canonical Module 0 catalog paths unless a specific
# test overrides them. Pin before importing the FastAPI app and repositories
# because several Genie canonical SQL constants are built at module import.
settings.mip_default_catalog = "mip"

from backend.api.config import _reset_config_cache_for_tests
from backend.main import _backpressure_controller, app
from backend.services.admin_rules import (
    AdminRulesService,
    _reset_admin_rules_service_for_tests,
    get_admin_rules_service,
)
from backend.services.asset_metadata import (
    AssetMetadataService,
    _reset_asset_metadata_service_for_tests,
    get_asset_metadata_service,
)
from backend.services.audit_store import (
    _reset_audit_store_for_tests,
    get_audit_store,
)
from backend.services.databricks_sql import _reset_sql_client_for_tests
from backend.services.genie_client import _reset_genie_client_for_tests
from backend.services.lakebase import (
    _reset_client_for_tests as _reset_lakebase_client_for_tests,
)
from backend.services.lakebase import (
    get_lakebase_client,
)
from backend.services.repositories import (
    get_analytics_repository,
    get_borrower_repository,
    get_genie_answer_repository,
    get_geo_repository,
    get_lead_repository,
    get_offer_repository,
    get_outreach_repository,
    get_portfolio_repository,
    get_segment_repository,
)
from backend.services.repositories.factory import _reset_singletons_for_tests
from backend.services.resilience import _reset_breakers_for_tests
from backend.services.workspace_store import (
    _reset_workspace_store_for_tests,
    get_workspace_store,
)
from tests.fixtures.in_memory_audit_store import InMemoryAuditStore
from tests.fixtures.in_memory_workspace_store import InMemoryWorkspaceStore
from tests.fixtures.in_process_repos import (
    InProcessMockAnalyticsRepository,
    InProcessMockBorrowerRepository,
    InProcessMockGenieAnswerRepository,
    InProcessMockGeoRepository,
    InProcessMockLeadRepository,
    InProcessMockOfferRepository,
    InProcessMockOutreachRepository,
    InProcessMockPortfolioRepository,
    InProcessMockSegmentRepository,
)


class _FakeLakebaseClient:
    """Test-only Lakebase stand-in.

    Records every execute / fetchone / fetchall call so assertions
    can introspect what would have been written. Returns a
    deterministic shape from ``fetchone`` (used by the Lakebase audit
    store's INSERT ... RETURNING) and an empty list from ``fetchall``.
    """

    def __init__(self) -> None:
        self.sales_team: list[dict[str, Any]] = [
            {
                "email": "skyler@entrada.ai",
                "display_label": "Entrada Demo Operator",
                "role": "admin",
                "manager_email": None,
                "region": "National",
                "capacity_per_day": 0,
                "active": True,
            },
            {
                "email": "lo01@summit.example",
                "display_label": "Summit LO 01",
                "role": "loan_officer",
                "manager_email": "skyler@entrada.ai",
                "region": "IL",
                "capacity_per_day": 35,
                "active": True,
            },
            {
                "email": "lo02@summit.example",
                "display_label": "Summit LO 02",
                "role": "loan_officer",
                "manager_email": "skyler@entrada.ai",
                "region": "CA",
                "capacity_per_day": 35,
                "active": True,
            },
        ]
        self.reset_for_test()

    def reset_for_test(self) -> None:
        self.executes: list[tuple[str, dict[str, Any]]] = []
        self.fetchones: list[tuple[str, dict[str, Any]]] = []
        self.fetchalls: list[tuple[str, dict[str, Any], int]] = []
        self.assignments: list[dict[str, Any]] = []
        self.dispositions: list[dict[str, Any]] = []
        self.approvals: list[dict[str, Any]] = []
        self.audit_events: list[dict[str, Any]] = []

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.executes.append((sql, params or {}))
        if "INSERT INTO mip_app.approvals" in sql and params:
            self.approvals.append(
                {
                    "approval_id": params.get("approval_id", uuid4()),
                    "borrower_id": params.get("borrower_id"),
                    "action": params.get("action", "approve"),
                    "offer_code": params.get("offer_code"),
                    "actor_email": params.get("actor_email"),
                    "rationale": params.get("rationale"),
                    "decided_at": datetime.now(UTC),
                    "request_id": params.get("request_id"),
                }
            )
        if "UPDATE mip_app.call_dispositions" in sql and params:
            for row in self.dispositions:
                if str(row["disposition_id"]) == str(params.get("disposition_id")):
                    row["audit_event_id"] = params.get("audit_event_id")

    def executemany(self, sql: str, params_list: list[dict[str, Any]]) -> None:
        for p in params_list:
            self.executes.append((sql, p))

    def fetchone(
        self, sql: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        self.fetchones.append((sql, params or {}))
        if "FROM mip_app.sales_team" in sql:
            email = str((params or {}).get("email") or "").lower()
            for row in self.sales_team:
                if row["email"] == email and row["active"]:
                    return dict(row)
            return None
        if "FROM mip_app.lead_assignments" in sql and "WHERE a.borrower_id" in sql:
            borrower_id = (params or {}).get("borrower_id")
            for row in reversed(self.assignments):
                if row["borrower_id"] == borrower_id and row.get("released_at") is None:
                    team = next((t for t in self.sales_team if t["email"] == row["assigned_to_email"]), {})
                    return {**row, "assigned_to_label": team.get("display_label")}
            return None
        if "WITH latest_approval" in sql:
            borrower_id = (params or {}).get("borrower_id")
            approvals = [row for row in self.approvals if row.get("borrower_id") == borrower_id]
            approval = approvals[-1] if approvals else None
            dispositions = [row for row in self.dispositions if row.get("borrower_id") == borrower_id]
            disposition = dispositions[-1] if dispositions else None
            action = approval.get("action") if approval else None
            approval_status = (
                "approved" if action == "approve"
                else "rejected" if action == "reject"
                else "hold" if action == "hold"
                else "pending"
            )
            return {
                "approval_status": approval_status,
                "outreach_status": "actioned" if disposition else ("queued" if action == "approve" else "none"),
                "approved_at": approval.get("decided_at") if action == "approve" else None,
                "outreach_at": disposition.get("occurred_at") if disposition else None,
                "synced_at": datetime.now(UTC),
            }
        if "FROM mip_app.call_dispositions" in sql and "ORDER BY occurred_at DESC" in sql:
            borrower_id = (params or {}).get("borrower_id")
            rows = [r for r in self.dispositions if r["borrower_id"] == borrower_id]
            return dict(rows[-1]) if rows else None
        if "FROM mip_app.approvals" in sql and "request_id" in sql:
            return None
        if "FROM mip_app.tenant_disclosures" in sql:
            channel = (params or {}).get("channel", "email")
            body = (
                "Summit Mortgage NMLS #123456. Equal Housing Lender. Reply STOP to opt out."
                if channel == "sms"
                else "Summit Mortgage, NMLS #123456. Equal Housing Lender. Reply unsubscribe to opt out."
            )
            return {
                "state": (params or {}).get("state", "_ALL"),
                "channel": channel,
                "disclosure_version": "test-disclosure-v1",
                "body": body,
            }
        if "INSERT INTO mip_app.campaigns" in sql:
            return {
                "campaign_id": uuid4(),
                "audit_id": uuid4(),
                "event_at": datetime.now(UTC),
            }
        if "FROM mip_app.campaigns" in sql:
            return {
                "campaign_id": (params or {}).get("campaign_id", uuid4()),
                "name": "Synthetic campaign",
                "owner_email": "skyler@entrada.ai",
                "status": "draft",
                "criteria": {},
                "suppression_policy": {"default": "eligible_only"},
                "message_variants": [],
                "channel_cascade": [],
                "send_window": {},
                "holdout": None,
                "roi_assumptions": None,
                "created_at": datetime.now(UTC),
                "updated_at": datetime.now(UTC),
            }
        return {"audit_id": uuid4(), "event_at": datetime.now(UTC)}

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, params or {}, limit))
        if "FROM mip_app.sales_team" in sql:
            return [dict(row) for row in self.sales_team if row["active"]][:limit]
        if "FROM mip_app.lead_assignments a" in sql:
            if "a.request_id" in sql:
                request_id = (params or {}).get("request_id")
                out = []
                for row in self.assignments:
                    if row.get("request_id") == request_id and row.get("released_at") is None:
                        team = next((t for t in self.sales_team if t["email"] == row["assigned_to_email"]), {})
                        out.append({**row, "assigned_to_label": team.get("display_label")})
                return out[:limit]
            borrower_ids = set((params or {}).get("borrower_ids") or [])
            out = []
            for row in self.assignments:
                if row["borrower_id"] in borrower_ids and row.get("released_at") is None:
                    team = next((t for t in self.sales_team if t["email"] == row["assigned_to_email"]), {})
                    out.append({**row, "assigned_to_label": team.get("display_label")})
            return out[:limit]
        if "FROM mip_app.lead_assignments" in sql and "assigned_to_email" in sql:
            assigned = str((params or {}).get("assigned_to_email") or "").lower()
            return [
                {"borrower_id": row["borrower_id"]}
                for row in self.assignments
                if row["assigned_to_email"] == assigned and row.get("released_at") is None
            ][:limit]
        if (
            "FROM mip_app.call_dispositions" in sql
            and "applications_started" in sql
            and "GROUP BY lo_email" in sql
        ):
            by_lo: dict[str, dict[str, int]] = {}
            for row in self.dispositions:
                lo_email = str(row["lo_email"])
                outcome = str(row["outcome"])
                bucket = by_lo.setdefault(
                    lo_email,
                    {
                        "calls_attempted": 0,
                        "contacts_reached": 0,
                        "callbacks_scheduled": 0,
                        "applications_started": 0,
                    },
                )
                bucket["calls_attempted"] += 1
                if outcome in {"connected", "callback_scheduled", "application_started"}:
                    bucket["contacts_reached"] += 1
                if outcome == "callback_scheduled":
                    bucket["callbacks_scheduled"] += 1
                if outcome == "application_started":
                    bucket["applications_started"] += 1
            return [
                {"group_key": lo_email, **counts}
                for lo_email, counts in sorted(by_lo.items())
            ][:limit]
        if "FROM mip_app.call_dispositions" in sql and "GROUP BY lo_email, outcome" in sql:
            counts: dict[tuple[str, str], int] = {}
            for row in self.dispositions:
                key = (str(row["lo_email"]), str(row["outcome"]))
                counts[key] = counts.get(key, 0) + 1
            return [
                {"lo_email": lo_email, "outcome": outcome, "n": n}
                for (lo_email, outcome), n in sorted(counts.items())
            ][:limit]
        if "FROM mip_app.call_dispositions" in sql and "GROUP BY 1" in sql:
            by_lo: dict[str, dict[str, int]] = {}
            for row in self.dispositions:
                lo_email = str(row["lo_email"])
                outcome = str(row["outcome"])
                bucket = by_lo.setdefault(
                    lo_email,
                    {
                        "calls_attempted": 0,
                        "contacts_reached": 0,
                        "callbacks_scheduled": 0,
                        "applications_started": 0,
                    },
                )
                bucket["calls_attempted"] += 1
                if outcome in {"connected", "callback_scheduled", "application_started"}:
                    bucket["contacts_reached"] += 1
                if outcome == "callback_scheduled":
                    bucket["callbacks_scheduled"] += 1
                if outcome == "application_started":
                    bucket["applications_started"] += 1
            return [
                {"group_key": lo_email, **counts}
                for lo_email, counts in sorted(by_lo.items())
            ][:limit]
        if "WITH latest_approval" in sql and "age_days" in sql:
            older_than_days = int((params or {}).get("older_than_days") or 7)
            now = datetime.now(UTC)
            latest: dict[str, dict[str, Any]] = {}
            for row in self.approvals:
                if row.get("action") != "approve":
                    continue
                decided_at = row.get("decided_at") or now
                if (now - decided_at).days < older_than_days:
                    continue
                borrower_id = str(row.get("borrower_id") or "")
                prior = latest.get(borrower_id)
                if prior is None or decided_at > (prior.get("decided_at") or now):
                    latest[borrower_id] = row
            out: list[dict[str, Any]] = []
            for borrower_id, row in latest.items():
                latest_disposition = max(
                    (d for d in self.dispositions if d.get("borrower_id") == borrower_id),
                    key=lambda d: d.get("occurred_at") or now,
                    default=None,
                )
                if latest_disposition is not None:
                    continue
                assignment = next(
                    (
                        a for a in self.assignments
                        if a.get("borrower_id") == borrower_id and a.get("released_at") is None
                    ),
                    None,
                )
                decided_at = row.get("decided_at") or now
                out.append(
                    {
                        "borrower_id": borrower_id,
                        "approval_status": "approved",
                        "approved_at": decided_at,
                        "age_days": (now - decided_at).days,
                        "outreach_status": "queued",
                        "outreach_at": None,
                        "assigned_to_email": assignment.get("assigned_to_email") if assignment else None,
                        "latest_disposition_outcome": None,
                        "latest_disposition_at": None,
                    }
                )
            return sorted(out, key=lambda r: r["approved_at"])[:limit]
        if "FROM mip_app.call_dispositions" in sql and "DISTINCT ON" in sql:
            borrower_ids = set((params or {}).get("borrower_ids") or [])
            out = []
            for row in self.dispositions:
                if row["borrower_id"] in borrower_ids:
                    out.append(dict(row))
            return out[:limit]
        if "FROM mip_app.campaigns" in sql:
            return [
                {
                    "campaign_id": uuid4(),
                    "name": "Synthetic campaign",
                    "owner_email": "skyler@entrada.ai",
                    "status": "draft",
                    "criteria": {},
                    "suppression_policy": {"default": "eligible_only"},
                    "message_variants": [],
                    "channel_cascade": [],
                    "send_window": {},
                    "holdout": None,
                    "roi_assumptions": None,
                    "created_at": datetime.now(UTC),
                    "updated_at": datetime.now(UTC),
                }
            ]
        return []

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        client = self

        class _Cursor:
            def __init__(self) -> None:
                self._last: dict[str, Any] | None = None

            def __enter__(self) -> _Cursor:
                return self

            def __exit__(self, *_exc: object) -> None:
                return None

            def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
                params = params or {}
                client.executes.append((sql, params))
                now = datetime.now(UTC)
                if "UPDATE mip_app.lead_assignments" in sql:
                    for row in client.assignments:
                        if row["borrower_id"] == params.get("borrower_id") and row.get("released_at") is None:
                            row["released_at"] = now
                    self._last = None
                elif "INSERT INTO mip_app.approvals" in sql:
                    row = {
                        "approval_id": params.get("approval_id", uuid4()),
                        "borrower_id": params.get("borrower_id"),
                        "action": params.get("action", "approve"),
                        "offer_code": params.get("offer_code"),
                        "actor_email": params.get("actor_email"),
                        "rationale": params.get("rationale"),
                        "decided_at": now,
                        "request_id": params.get("request_id"),
                    }
                    client.approvals.append(row)
                    self._last = {"approval_id": row["approval_id"]}
                elif "INSERT INTO mip_app.lead_assignments" in sql:
                    row = {
                        "assignment_id": uuid4(),
                        "borrower_id": params["borrower_id"],
                        "assigned_to_email": params["assigned_to_email"],
                        "assigned_by": params["assigned_by"],
                        "assigned_at": now,
                        "expires_at": (
                            now + timedelta(hours=int(params["expires_in_hours"]))
                            if params.get("expires_in_hours") is not None
                            else None
                        ),
                        "released_at": None,
                        "strategy": params.get("strategy") or "manual",
                        "request_id": params.get("request_id"),
                    }
                    client.assignments.append(row)
                    self._last = row
                elif "MAX(attempt_number)" in sql:
                    borrower_id = params.get("borrower_id")
                    attempts = [r["attempt_number"] for r in client.dispositions if r["borrower_id"] == borrower_id]
                    self._last = {"next_attempt": (max(attempts) if attempts else 0) + 1}
                elif "INSERT INTO mip_app.call_dispositions" in sql:
                    row = {
                        "disposition_id": uuid4(),
                        "borrower_id": params["borrower_id"],
                        "lo_email": params["lo_email"],
                        "outcome": params["outcome"],
                        "attempt_number": params["attempt_number"],
                        "occurred_at": params.get("occurred_at") or now,
                        "callback_at": params.get("callback_at"),
                        "notes": params.get("notes"),
                        "audit_event_id": None,
                        "request_id": params.get("request_id"),
                        "created_at": now,
                    }
                    client.dispositions.append(row)
                    self._last = row
                elif "INSERT INTO mip_app.action_audit" in sql:
                    row = {
                        "audit_id": uuid4(),
                        "event_at": now,
                        **params,
                    }
                    client.audit_events.append(row)
                    self._last = row
                elif "UPDATE mip_app.call_dispositions" in sql:
                    for row in client.dispositions:
                        if str(row["disposition_id"]) == str(params.get("disposition_id")):
                            row["audit_event_id"] = params.get("audit_event_id")
                    self._last = None
                else:
                    self._last = None

            def fetchone(self) -> dict[str, Any] | None:
                return self._last

        class _Conn:
            def cursor(self) -> _Cursor:
                return _Cursor()

        yield _Conn()


class _FakeAdminSqlClient:
    """Test-only SQL client for the admin-rules service.

    Pre-programs two query shapes the service issues:

    * ``SELECT ... FROM mip.ref.offer_rules_config`` -> canonical five
      threshold rows mirroring the seed file.
    * ``SELECT ... FROM mip.gold.borrower_360`` -> operating market-rate row
      injected into the admin rules response.
    * ``DESCRIBE DETAIL`` / ``SELECT COUNT(*)`` against each silver
      table -> deterministic row counts + a fixed ``lastModified``
      timestamp so the sources endpoint reports LIVE for every wired
      source in unit tests.

    Any query not matching the above returns an empty list; tests that
    want to exercise 503 paths install their own fake that raises.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def execute(
        self,
        statement: str,
        parameters: Any = None,
    ) -> list[dict[str, Any]]:
        self.calls.append(statement)
        s = statement.strip().upper()
        # Catalog-agnostic match: ``.REF.OFFER_RULES_CONFIG`` so the stub
        # keeps matching when ``MIP_DEFAULT_CATALOG`` is overridden (e.g.
        # ``mip_demo`` locally, ``mip_prod`` in prod deploys).
        if ".REF.OFFER_RULES_CONFIG" in s:
            return [
                {"key": "mip_min_spread_bps",           "value": 75.0,    "unit": "bps",           "label": "Min spread (bps)",            "description": "desc", "sort_order": 1, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_min_equity_pct",           "value": 15.0,    "unit": "pct",           "label": "Min equity (%)",              "description": "desc", "sort_order": 2, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_heloc_equity_min_pct",     "value": 35.0,    "unit": "pct",           "label": "HELOC equity floor (%)",      "description": "desc", "sort_order": 3, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_cashout_equity_min_pct",   "value": 25.0,    "unit": "pct",           "label": "Cash-out equity floor (%)",   "description": "desc", "sort_order": 4, "last_updated": "2026-04-22 12:00:00"},
                {"key": "mip_retention_min_spread_bps", "value": 50.0,    "unit": "bps",           "label": "Retention min spread (bps)",  "description": "desc", "sort_order": 5, "last_updated": "2026-04-22 12:00:00"},
            ]
        if ".GOLD.BORROWER_360" in s and "MARKET_RATE_FRACTION" in s:
            return [{"rate_fraction": 0.0637, "last_updated": "2026-05-07 12:00:00"}]
        if s.startswith("DESCRIBE DETAIL"):
            return [{"lastModified": "2026-04-22T12:00:00.000Z"}]
        if "COUNT(*)" in s and "FROM" in s:
            return [{"row_count": 1000}]
        return []

    def execute_one(
        self,
        statement: str,
        parameters: Any = None,
    ) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


class _FakeAssetSqlClient:
    """Test-only SQL client for governed asset metadata."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, Any]] = []

    def execute(self, statement: str, parameters: Any = None) -> list[dict[str, Any]]:
        self.calls.append((statement, parameters))
        s = statement.strip().upper()
        if "SYSTEM.INFORMATION_SCHEMA.TABLES" in s:
            return [
                {
                    "table_catalog": "mip",
                    "table_schema": "gold",
                    "table_name": "lead_population",
                    "table_type": "MANAGED",
                    "comment": "Ranked lead population",
                }
            ]
        if "GOLD.SOURCE_READINESS" in s:
            return [
                {
                    "source_name": "UC Gold Lead Population",
                    "status": "live",
                    "row_count": 5156184,
                    "last_updated": "2026-05-20 12:00:00",
                    "checked_at": "2026-05-20 12:30:00",
                    "note": "Ranked lead queue table · refreshed",
                    "source_table": "mip.gold.lead_population",
                }
            ]
        if s.startswith("DESCRIBE DETAIL"):
            return [
                {
                    "numRecords": 5156184,
                    "numFiles": 42,
                    "sizeInBytes": 987654321,
                    "lastModified": "2026-05-20T12:15:00.000Z",
                    "location": "dbfs:/not-returned",
                }
            ]
        if "SYSTEM.INFORMATION_SCHEMA.COLUMNS" in s:
            return [
                {
                    "column_name": "borrower_id",
                    "ordinal_position": 1,
                    "full_data_type": "STRING",
                    "data_type": "STRING",
                    "is_nullable": "NO",
                    "comment": "Masked borrower id",
                },
                {
                    "column_name": "opportunity_score",
                    "ordinal_position": 2,
                    "full_data_type": "INT",
                    "data_type": "INT",
                    "is_nullable": "YES",
                    "comment": "Deterministic score",
                },
                {
                    "column_name": "owner_name_hash",
                    "ordinal_position": 3,
                    "full_data_type": "STRING",
                    "data_type": "STRING",
                    "is_nullable": "YES",
                    "comment": "sensitive",
                },
            ]
        if "SYSTEM.INFORMATION_SCHEMA.TABLE_TAGS" in s:
            return [
                {"tag_name": "data_classification", "tag_value": "masked"},
                {"tag_name": "owner_email", "tag_value": "person@example.com"},
            ]
        if s.startswith("SHOW TBLPROPERTIES"):
            return [
                {"key": "quality", "value": "gold"},
                {"key": "path", "value": "dbfs:/secret"},
            ]
        if "SYSTEM.ACCESS.TABLE_LINEAGE" in s:
            return [
                {
                    "source_table_full_name": "mip.gold.borrower_360",
                    "target_table_full_name": "mip.gold.lead_population",
                    "event_time": "2026-05-20 12:15:00",
                    "event_count": 2,
                }
            ]
        if "COUNT(*)" in s and "FROM" in s:
            return [{"row_count": 5156184}]
        return []

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        rows = self.execute(statement, parameters)
        return rows[0] if rows else None


# -----------------------------------------------------------------------
# Admin-header auto-injection. Slice-RBAC wired ``require_admin`` onto
# every ``/api/admin/*`` route, so unit tests that hit the admin surface
# must now carry an ``X-Forwarded-Groups`` header including ``mip-admin``.
#
# Rather than thread ``headers=...`` through every call site, we wrap
# ``TestClient.__init__`` at conftest import time (NOT inside a fixture)
# so any module that does ``client = TestClient(app)`` at import time --
# the existing pattern in ``test_api_routes.py`` and
# ``test_admin_rules.py`` -- picks up the default header. Because
# ``conftest.py`` is imported by pytest before collection of peer
# modules, the wrap is in place before those module-level clients are
# constructed.
#
# Tests that need to exercise the DENY path (403 on missing / wrong
# group) pass ``headers={"X-Forwarded-Groups": ""}`` explicitly on the
# call -- httpx merges per-call headers over instance defaults, so an
# empty-value override wins.
# -----------------------------------------------------------------------


_ADMIN_HEADERS: dict[str, str] = {"X-Forwarded-Groups": "mip-admin"}
_BASE_DEPENDENCY_OVERRIDES: dict[Any, Any] = {}


def _reset_runtime_singletons_for_tests() -> None:
    _backpressure_controller.clear()
    _reset_singletons_for_tests()
    _reset_sql_client_for_tests()
    _reset_lakebase_client_for_tests()
    _reset_genie_client_for_tests()
    _reset_audit_store_for_tests()
    _reset_workspace_store_for_tests()
    _reset_admin_rules_service_for_tests()
    _reset_asset_metadata_service_for_tests()
    _reset_config_cache_for_tests()
    _reset_breakers_for_tests()


def _reset_fake_dependency_state_for_tests() -> None:
    factory = _BASE_DEPENDENCY_OVERRIDES.get(get_lakebase_client)
    if factory is None:
        return
    client = factory()
    reset = getattr(client, "reset_for_test", None)
    if callable(reset):
        reset()


def _wrap_testclient_with_admin_headers() -> None:
    """One-shot wrap of ``TestClient.__init__`` applied at conftest load.

    Idempotent -- re-running under pytest-watch/pytest-xdist is a no-op
    because we set a sentinel on the class.
    """
    if getattr(TestClient, "_mip_admin_header_wrap_installed", False):
        return
    original_init = TestClient.__init__

    def _patched_init(self: TestClient, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        for k, v in _ADMIN_HEADERS.items():
            self.headers.setdefault(k, v)

    TestClient.__init__ = _patched_init  # type: ignore[method-assign]
    TestClient._mip_admin_header_wrap_installed = True  # type: ignore[attr-defined]


_wrap_testclient_with_admin_headers()


@pytest.fixture(scope="session", autouse=True)
def _install_dependency_overrides() -> Iterator[None]:
    """Swap every live repository factory for its in-process stub.

    ``scope="session"`` + ``autouse=True`` means the override is active
    for the whole test run -- no individual test needs to remember to
    apply it. Teardown restores the original (empty) overrides dict so
    pytest can be re-entered cleanly in watch mode.
    """
    portfolio = InProcessMockPortfolioRepository()
    analytics = InProcessMockAnalyticsRepository()
    segment = InProcessMockSegmentRepository()
    lead = InProcessMockLeadRepository()
    borrower = InProcessMockBorrowerRepository()
    offer = InProcessMockOfferRepository()
    outreach = InProcessMockOutreachRepository()
    genie = InProcessMockGenieAnswerRepository()
    geo = InProcessMockGeoRepository()
    audit = InMemoryAuditStore()
    lakebase = _FakeLakebaseClient()
    workspace = InMemoryWorkspaceStore()
    admin_rules = AdminRulesService(_FakeAdminSqlClient())
    asset_metadata = AssetMetadataService(_FakeAssetSqlClient())

    app.dependency_overrides[get_portfolio_repository] = lambda: portfolio
    app.dependency_overrides[get_analytics_repository] = lambda: analytics
    app.dependency_overrides[get_segment_repository] = lambda: segment
    app.dependency_overrides[get_lead_repository] = lambda: lead
    app.dependency_overrides[get_borrower_repository] = lambda: borrower
    app.dependency_overrides[get_offer_repository] = lambda: offer
    app.dependency_overrides[get_outreach_repository] = lambda: outreach
    app.dependency_overrides[get_genie_answer_repository] = lambda: genie
    app.dependency_overrides[get_geo_repository] = lambda: geo
    app.dependency_overrides[get_audit_store] = lambda: audit
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_workspace_store] = lambda: workspace
    app.dependency_overrides[get_admin_rules_service] = lambda: admin_rules
    app.dependency_overrides[get_asset_metadata_service] = lambda: asset_metadata
    _BASE_DEPENDENCY_OVERRIDES.clear()
    _BASE_DEPENDENCY_OVERRIDES.update(app.dependency_overrides)
    try:
        yield
    finally:
        app.dependency_overrides.clear()
        _BASE_DEPENDENCY_OVERRIDES.clear()


@pytest.fixture(autouse=True)
def _isolate_fastapi_dependency_state() -> Iterator[None]:
    """Keep per-test FastAPI overrides and breakers from leaking.

    A few live-gated integration tests intentionally clear
    ``app.dependency_overrides`` to exercise production wiring. If a
    setup failure happens before their local ``finally`` runs, later unit
    tests can accidentally hit live Lakebase / warehouse factories. The
    suite also uses process-global repository/client singletons and
    circuit breakers, so a singleton built during one test can poison an
    unrelated endpoint assertion. Reset the production singleton caches
    around every test; the shared fake service instances themselves are
    preserved by the session fixture.
    """

    # Restore from the canonical in-process baseline, not from whatever
    # an earlier test may have accidentally left in the global dict.
    app.dependency_overrides.clear()
    app.dependency_overrides.update(_BASE_DEPENDENCY_OVERRIDES)
    _reset_fake_dependency_state_for_tests()
    _reset_runtime_singletons_for_tests()
    original_catalog = settings.mip_default_catalog
    settings.mip_default_catalog = "mip"
    try:
        yield
    finally:
        settings.mip_default_catalog = original_catalog
        app.dependency_overrides.clear()
        app.dependency_overrides.update(_BASE_DEPENDENCY_OVERRIDES)
        _reset_fake_dependency_state_for_tests()
        _reset_runtime_singletons_for_tests()
