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

import json
import os
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
from backend.services.home_summary import (
    HomeSummaryService,
    get_home_summary_service,
)
from backend.services.home_summary import (
    _reset_service_for_tests as _reset_home_summary_service_for_tests,
)
from backend.services.kpi_deltas import KpiDeltaService
from backend.services.kpi_deltas import (
    _reset_service_for_tests as _reset_kpi_delta_service_for_tests,
)
from backend.services.lakebase import (
    _reset_client_for_tests as _reset_lakebase_client_for_tests,
)
from backend.services.geo_assignment_overlay import (
    get_geo_assignment_overlay_service,
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
from backend.services.sales_state import clear_sales_state_cache
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
    InProcessMockGeoOverlayService,
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
        # S2 loan-officer entity roster; ids mirror lakebase/seed_campaigns.sql.
        self.loan_officers: list[dict[str, Any]] = [
            {
                "loan_officer_id": "55555555-5555-4555-8555-555555555501",
                "email": "lo01@summit.example",
                "display_name": "Summit LO 01",
                "coverage_states": ["IL", "IN", "WI"],
                "coverage_counties": ["17031", "17043", "17089"],
                "active": True,
            },
            {
                "loan_officer_id": "55555555-5555-4555-8555-555555555502",
                "email": "lo02@summit.example",
                "display_name": "Summit LO 02",
                "coverage_states": ["CA", "NV"],
                "coverage_counties": ["06037", "06059", "06073"],
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
        self.outcomes: list[dict[str, Any]] = []
        self.campaigns: list[dict[str, Any]] = []
        self.activation_destinations: list[dict[str, Any]] = [
            {
                "source_system": "salesforce",
                "destination_type": "salesforce",
                "display_name": "Salesforce CRM",
                "status": "not_configured",
            },
            {
                "source_system": "crm_cdp",
                "destination_type": "crm_cdp",
                "display_name": "Customer CRM / CDP",
                "status": "not_configured",
            },
            {
                "source_system": "los_pos",
                "destination_type": "los_pos",
                "display_name": "LOS / POS",
                "status": "not_configured",
            },
            {
                "source_system": "servicing",
                "destination_type": "servicing",
                "display_name": "Servicing platform",
                "status": "not_configured",
            },
        ]
        self.approvals: list[dict[str, Any]] = []
        self.generated_outreach_drafts: list[dict[str, Any]] = []
        self.audit_events: list[dict[str, Any]] = []
        # S6 assignment outcomes reuse the feedback table pattern.
        self.feedback: list[dict[str, Any]] = []

    def _loan_officer_assignment_row(self, row: dict[str, Any]) -> dict[str, Any]:
        officer = next(
            (
                o
                for o in self.loan_officers
                if o["loan_officer_id"] == str(row.get("loan_officer_id") or "")
            ),
            None,
        )
        return {
            **row,
            "loan_officer_email": row.get("assigned_to_email"),
            "loan_officer_name": officer.get("display_name") if officer else None,
            "status": row.get("status") or "assigned",
        }

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
                    "assigned_to_email": params.get("assigned_to_email"),
                    "follow_up_at": params.get("follow_up_at"),
                    "decision_intent": params.get("decision_intent"),
                    "decision_payload_hash": params.get("decision_payload_hash"),
                    "decision_response": None,
                    "audit_event_id": None,
                }
            )
        if "UPDATE mip_app.approvals" in sql and params:
            for row in self.approvals:
                if str(row["approval_id"]) == str(params.get("approval_id")):
                    row["decision_response"] = json.loads(
                        str(params.get("decision_response") or "{}")
                    )
                    row["audit_event_id"] = params.get("audit_event_id")
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
        if "INSERT INTO mip_app.generated_outreach_drafts" in sql:
            values = params or {}
            response_json = json.loads(str(values.get("response_json") or "{}"))
            row = {
                "generation_id": values["generation_id"],
                "audit_event_id": values["audit_id"],
                "response_hash": values["response_hash"],
                "response_json": response_json,
            }
            self.generated_outreach_drafts.append({**values, **row})
            self.audit_events.append(
                {
                    "audit_id": values["audit_id"],
                    "event_at": datetime.now(UTC),
                    **values,
                }
            )
            return row
        if "FROM mip_app.generated_outreach_drafts" in sql:
            values = params or {}
            for row in self.generated_outreach_drafts:
                if (
                    str(row.get("generation_id") or "")
                    == str(values.get("generation_id") or "")
                    and str(row.get("actor_email") or "")
                    == str(values.get("actor_email") or "")
                    and str(row.get("borrower_id") or "")
                    == str(values.get("borrower_id") or "")
                ):
                    return {
                        "generation_id": row.get("generation_id"),
                        "audit_event_id": row.get("audit_event_id"),
                        "actor_email": row.get("actor_email"),
                        "borrower_id": row.get("borrower_id"),
                        "channel": row.get("channel"),
                        "offer_code": row.get("offer_code"),
                        "generation_mode": row.get("generation_mode"),
                        "response_hash": row.get("response_hash"),
                        "response_json": row.get("response_json"),
                    }
            return None
        if "FROM mip_app.sales_team" in sql:
            email = str((params or {}).get("email") or "").lower()
            for row in self.sales_team:
                if row["email"] == email and row["active"]:
                    return dict(row)
            return None
        if "FROM mip_app.loan_officers" in sql:
            loan_officer_id = str((params or {}).get("loan_officer_id") or "")
            for row in self.loan_officers:
                if row["loan_officer_id"] == loan_officer_id and row["active"]:
                    return dict(row)
            return None
        if "FROM mip_app.lead_assignments a" in sql and "WHERE a.assignment_id" in sql:
            assignment_id = str((params or {}).get("assignment_id") or "")
            for row in self.assignments:
                if str(row["assignment_id"]) == assignment_id:
                    return self._loan_officer_assignment_row(row)
            return None
        if "FROM mip_app.lead_assignments a" in sql and "WHERE a.request_id" in sql:
            request_id = (params or {}).get("request_id")
            for row in self.assignments:
                if row.get("request_id") == request_id:
                    return self._loan_officer_assignment_row(row)
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
                "approval_id": approval.get("approval_id") if action == "approve" else None,
                "approved_at": approval.get("decided_at") if action == "approve" else None,
                "outreach_at": disposition.get("occurred_at") if disposition else None,
                "synced_at": datetime.now(UTC),
            }
        if "FROM mip_app.call_dispositions" in sql and "WHERE request_id" in sql:
            request_id = (params or {}).get("request_id")
            for row in self.dispositions:
                if row.get("request_id") == request_id:
                    return dict(row)
            return None
        if "FROM mip_app.call_dispositions" in sql and "ORDER BY occurred_at DESC" in sql:
            borrower_id = (params or {}).get("borrower_id")
            rows = [r for r in self.dispositions if r["borrower_id"] == borrower_id]
            return dict(rows[-1]) if rows else None
        if "FROM mip_app.lead_outcomes" in sql and "WHERE request_id" in sql:
            request_id = (params or {}).get("request_id")
            for row in self.outcomes:
                if row.get("request_id") == request_id:
                    return dict(row)
            return None
        if "FROM mip_app.lead_outcomes" in sql and "WHERE source_system" in sql:
            source_system = (params or {}).get("source_system")
            source_record_ref = (params or {}).get("source_record_ref")
            for row in self.outcomes:
                if (
                    row.get("source_system") == source_system
                    and row.get("source_record_ref") == source_record_ref
                ):
                    return dict(row)
            return None
        if "FROM mip_app.activation_destinations" in sql and "WHERE destination_type" in sql:
            source_system = (params or {}).get("source_system")
            rows = [
                row
                for row in self.activation_destinations
                if row.get("destination_type") == source_system
            ]
            rows.sort(
                key=lambda row: {
                    "connected": 1,
                    "dry_run": 2,
                    "not_configured": 3,
                    "disabled": 4,
                }.get(str(row.get("status")), 5)
            )
            return dict(rows[0]) if rows else None
        if "FROM mip_app.feedback" in sql and "WHERE request_id" in sql:
            request_id = (params or {}).get("request_id")
            for row in self.feedback:
                if row.get("request_id") == request_id:
                    return dict(row)
            return None
        if "approved_union" in sql:
            # S6 funnel: distinct borrowers at-or-past 'approved' via the
            # active assignment lifecycle UNION human approve decisions.
            lifecycle_borrowers = {
                row["borrower_id"]
                for row in self.assignments
                if row.get("released_at") is None
                and (row.get("status") or "assigned")
                in {"approved", "actioned", "outcome_recorded"}
            }
            approval_borrowers = {
                row["borrower_id"]
                for row in self.approvals
                if row.get("action") == "approve"
            }
            return {"n": len(lifecycle_borrowers | approval_borrowers)}
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
        if (
            "FROM mip_app.campaigns c" in sql
            and "c.idempotency_key" in sql
            and "INSERT INTO mip_app.campaigns" not in sql
        ):
            return next(
                (
                    dict(row)
                    for row in self.campaigns
                    if row.get("owner_email") == (params or {}).get("owner_email")
                    and row.get("idempotency_key") == (params or {}).get("idempotency_key")
                ),
                None,
            )
        if "INSERT INTO mip_app.campaigns" in sql:
            owner_email = (params or {}).get("owner_email")
            idempotency_key = (params or {}).get("idempotency_key")
            existing = next(
                (
                    row
                    for row in self.campaigns
                    if row.get("owner_email") == owner_email
                    and row.get("idempotency_key") == idempotency_key
                ),
                None,
            )
            if existing is not None:
                return dict(existing)
            row = {
                "campaign_id": uuid4(),
                "audit_id": uuid4(),
                "event_at": datetime.now(UTC),
                "owner_email": owner_email,
                "idempotency_key": idempotency_key,
                "request_payload_hash": (params or {}).get("request_payload_hash"),
                "creation_response": json.loads(
                    str((params or {}).get("creation_response") or "{}")
                ),
                "created": True,
            }
            self.campaigns.append(row)
            return dict(row)
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
        if "FROM mip_app.user_visits" in sql or "FROM mip_app.kpi_snapshots" in sql:
            # S4 home summary: no recorded visits or snapshots in the fake ->
            # the delta service resolves the honest first-visit shape.
            return None
        return {"audit_id": uuid4(), "event_at": datetime.now(UTC)}

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        self.fetchalls.append((sql, params or {}, limit))
        if "GROUP BY COALESCE(status, 'assigned')" in sql:
            # S6 funnel: distinct borrowers per lifecycle status (active rows).
            by_status: dict[str, set[str]] = {}
            for row in self.assignments:
                if row.get("released_at") is not None:
                    continue
                status = str(row.get("status") or "assigned")
                by_status.setdefault(status, set()).add(str(row["borrower_id"]))
            return [
                {"status": status, "n": len(borrowers)}
                for status, borrowers in sorted(by_status.items())
            ][:limit]
        if "GROUP BY o.loan_officer_id" in sql:
            # S6 funnel: per-officer active-assignment counts by status,
            # LEFT JOIN semantics -- officers with no assignments still
            # emit one zero row.
            out = []
            for officer in self.loan_officers:
                if not officer["active"]:
                    continue
                counts: dict[str, int] = {}
                for row in self.assignments:
                    if row.get("released_at") is not None:
                        continue
                    if str(row.get("loan_officer_id") or "") != officer["loan_officer_id"]:
                        continue
                    status = str(row.get("status") or "assigned")
                    counts[status] = counts.get(status, 0) + 1
                if not counts:
                    counts = {"assigned": 0}
                for status, n in sorted(counts.items()):
                    out.append(
                        {
                            "loan_officer_id": officer["loan_officer_id"],
                            "display_name": officer["display_name"],
                            "email": officer["email"],
                            "status": status,
                            "n": n,
                        }
                    )
            return out[:limit]
        if "FROM mip_app.feedback f" in sql:
            # S6 funnel: per-officer recorded-outcome counts.
            loan_officer_id = str((params or {}).get("loan_officer_id") or "")
            assignment_ids = {
                str(row["assignment_id"])
                for row in self.assignments
                if str(row.get("loan_officer_id") or "") == loan_officer_id
            }
            counts: dict[str, int] = {}
            for row in self.feedback:
                event_type = str(row.get("event_type") or "")
                if not event_type.startswith("assignment_outcome_"):
                    continue
                if str(row.get("assignment_id")) not in assignment_ids:
                    continue
                counts[event_type] = counts.get(event_type, 0) + 1
            return [
                {"event_type": event_type, "n": n}
                for event_type, n in sorted(counts.items())
            ][:limit]
        if "FROM mip_app.approvals" in sql and "ORDER BY decided_at DESC" in sql:
            rows = [
                {
                    "approval_id": row.get("approval_id"),
                    "borrower_id": row.get("borrower_id"),
                    "offer_code": row.get("offer_code"),
                    "actor_email": row.get("actor_email"),
                    "assigned_to_email": row.get("assigned_to_email"),
                    "decided_at": row.get("decided_at"),
                }
                for row in self.approvals
                if row.get("action") == "approve"
            ]
            rows.sort(key=lambda r: r["decided_at"], reverse=True)
            return rows[:limit]
        if "FROM mip_app.sales_team" in sql:
            return [dict(row) for row in self.sales_team if row["active"]][:limit]
        if "FROM mip_app.loan_officers" in sql:
            return [dict(row) for row in self.loan_officers if row["active"]][:limit]
        if "LEFT JOIN mip_app.loan_officers o" in sql:
            loan_officer_id = (params or {}).get("loan_officer_id")
            borrower_id = (params or {}).get("borrower_id")
            status = (params or {}).get("status")
            out = []
            for row in self.assignments:
                if row.get("released_at") is not None:
                    continue
                if loan_officer_id and str(row.get("loan_officer_id") or "") != str(loan_officer_id):
                    continue
                if borrower_id and row.get("borrower_id") != borrower_id:
                    continue
                if status and (row.get("status") or "assigned") != status:
                    continue
                out.append(self._loan_officer_assignment_row(row))
            return out[:limit]
        if "FROM mip_app.lead_assignments a" in sql:
            if "a.request_id" in sql:
                request_id = (params or {}).get("request_id")
                out = []
                for row in self.assignments:
                    if row.get("request_id") == request_id:
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
            by_lo: dict[str, dict[str, Any]] = {}
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
                        "unique_lead_ids": set(),
                        "unique_contact_ids": set(),
                        "unique_application_ids": set(),
                    },
                )
                bucket["calls_attempted"] += 1
                bucket["unique_lead_ids"].add(str(row["borrower_id"]))
                if outcome in {"connected", "callback_scheduled", "application_started"}:
                    bucket["contacts_reached"] += 1
                    bucket["unique_contact_ids"].add(str(row["borrower_id"]))
                if outcome == "callback_scheduled":
                    bucket["callbacks_scheduled"] += 1
                if outcome == "application_started":
                    bucket["applications_started"] += 1
                    bucket["unique_application_ids"].add(str(row["borrower_id"]))
            return [
                {
                    "group_key": lo_email,
                    **{key: value for key, value in counts.items() if not key.endswith("_ids")},
                    "unique_leads_contacted": len(counts["unique_lead_ids"]),
                    "unique_contacts_reached": len(counts["unique_contact_ids"]),
                    "unique_application_starts": len(counts["unique_application_ids"]),
                }
                for lo_email, counts in sorted(by_lo.items())
            ][:limit]
        if "campaign_performance_funnel" in sql:
            lo_emails = set((params or {}).get("lo_emails") or [])
            scoped_dispositions = [
                row
                for row in self.dispositions
                if not lo_emails or str(row.get("lo_email")) in lo_emails
            ]
            attempted: dict[str, datetime] = {}
            for row in scoped_dispositions:
                borrower_id = str(row["borrower_id"])
                occurred_at = row["occurred_at"]
                if borrower_id not in attempted or occurred_at < attempted[borrower_id]:
                    attempted[borrower_id] = occurred_at
            reached: dict[str, datetime] = {}
            for row in scoped_dispositions:
                borrower_id = str(row["borrower_id"])
                occurred_at = row["occurred_at"]
                if (
                    row.get("outcome") in {"connected", "callback_scheduled", "application_started"}
                    and occurred_at >= attempted[borrower_id]
                    and (borrower_id not in reached or occurred_at < reached[borrower_id])
                ):
                    reached[borrower_id] = occurred_at
            started: dict[str, datetime] = {}
            for row in scoped_dispositions:
                borrower_id = str(row["borrower_id"])
                occurred_at = row["occurred_at"]
                if (
                    row.get("outcome") == "application_started"
                    and borrower_id in reached
                    and occurred_at >= reached[borrower_id]
                    and (borrower_id not in started or occurred_at < started[borrower_id])
                ):
                    started[borrower_id] = occurred_at
            scoped_outcomes = [
                row
                for row in self.outcomes
                if not lo_emails or str(row.get("assigned_to_email")) in lo_emails
            ]
            submitted: dict[str, datetime] = {}
            for row in scoped_outcomes:
                borrower_id = str(row["borrower_id"])
                occurred_at = row["occurred_at"]
                if (
                    row.get("outcome_type") == "application_submitted"
                    and borrower_id in started
                    and occurred_at >= started[borrower_id]
                    and (borrower_id not in submitted or occurred_at < submitted[borrower_id])
                ):
                    submitted[borrower_id] = occurred_at
            funded: dict[str, datetime] = {}
            for row in scoped_outcomes:
                borrower_id = str(row["borrower_id"])
                occurred_at = row["occurred_at"]
                if (
                    row.get("outcome_type") == "closed_funded"
                    and borrower_id in submitted
                    and occurred_at >= submitted[borrower_id]
                    and (borrower_id not in funded or occurred_at < funded[borrower_id])
                ):
                    funded[borrower_id] = occurred_at
            return [
                {
                    "unique_leads_attempted": len(attempted),
                    "unique_contacts_reached": len(reached),
                    "unique_application_starts": len(started),
                    "unique_applications_submitted": len(submitted),
                    "unique_closed_funded": len(funded),
                }
            ]
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
            by_group: dict[str, dict[str, Any]] = {}
            aggregate_all = "'all_cohorts' AS group_key" in sql
            for row in self.dispositions:
                group_key = "all_cohorts" if aggregate_all else str(row["lo_email"])
                outcome = str(row["outcome"])
                bucket = by_group.setdefault(
                    group_key,
                    {
                        "calls_attempted": 0,
                        "contacts_reached": 0,
                        "callbacks_scheduled": 0,
                        "applications_started": 0,
                        "unique_lead_ids": set(),
                        "unique_contact_ids": set(),
                        "unique_application_ids": set(),
                    },
                )
                bucket["calls_attempted"] += 1
                bucket["unique_lead_ids"].add(str(row["borrower_id"]))
                if outcome in {"connected", "callback_scheduled", "application_started"}:
                    bucket["contacts_reached"] += 1
                    bucket["unique_contact_ids"].add(str(row["borrower_id"]))
                if outcome == "callback_scheduled":
                    bucket["callbacks_scheduled"] += 1
                if outcome == "application_started":
                    bucket["applications_started"] += 1
                    bucket["unique_application_ids"].add(str(row["borrower_id"]))
            return [
                {
                    "group_key": group_key,
                    **{key: value for key, value in counts.items() if not key.endswith("_ids")},
                    "unique_leads_contacted": len(counts["unique_lead_ids"]),
                    "unique_contacts_reached": len(counts["unique_contact_ids"]),
                    "unique_application_starts": len(counts["unique_application_ids"]),
                }
                for group_key, counts in sorted(by_group.items())
            ][:limit]
        if (
            "FROM mip_app.lead_outcomes" in sql
            and "unique_applications_submitted" in sql
            and "GROUP BY" not in sql
        ):
            submitted = {
                str(row["borrower_id"])
                for row in self.outcomes
                if row.get("outcome_type") == "application_submitted"
            }
            funded = {
                str(row["borrower_id"])
                for row in self.outcomes
                if row.get("outcome_type") == "closed_funded"
            }
            return [{
                "unique_applications_submitted": len(submitted),
                "unique_closed_funded": len(funded),
            }]
        if "FROM mip_app.lead_outcomes" in sql and "GROUP BY outcome_type" in sql:
            counts: dict[tuple[str, str, str | None, str], int] = {}
            for row in self.outcomes:
                key = (
                    str(row["outcome_type"]),
                    str(row["source_system"]),
                    row.get("assigned_to_email"),
                    str(row.get("competitor_lender_label") or ""),
                )
                counts[key] = counts.get(key, 0) + 1
            return [
                {
                    "outcome_type": outcome_type,
                    "source_system": source_system,
                    "assigned_to_email": assigned_to_email,
                    "competitor_lender_label": competitor_lender_label,
                    "n": n,
                }
                for (outcome_type, source_system, assigned_to_email, competitor_lender_label), n
                in sorted(counts.items())
            ][:limit]
        if "FROM mip_app.activation_destinations" in sql and "destination_type IN" in sql:
            return [dict(row) for row in self.activation_destinations][:limit]
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
                if "UPDATE mip_app.lead_assignments" in sql and "SET status" in sql:
                    self._last = None
                    for row in client.assignments:
                        if (
                            str(row["assignment_id"]) == str(params.get("assignment_id"))
                            and (row.get("status") or "assigned") == params.get("from_status")
                            and row.get("released_at") is None
                        ):
                            row["status"] = params.get("to_status")
                            row["status_updated_at"] = now
                            self._last = client._loan_officer_assignment_row(row)
                elif "UPDATE mip_app.lead_assignments" in sql:
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
                        "assigned_to_email": params.get("assigned_to_email"),
                        "follow_up_at": params.get("follow_up_at"),
                    }
                    client.approvals.append(row)
                    self._last = {"approval_id": row["approval_id"]}
                elif "INSERT INTO mip_app.lead_assignments" in sql:
                    request_id = params.get("request_id")
                    assignment_scope = str(params.get("assignment_scope") or "single")
                    if request_id:
                        duplicate = next(
                            (
                                row
                                for row in client.assignments
                                if row.get("request_id") == request_id
                                and (
                                    (
                                        assignment_scope == "single"
                                        and row.get("assignment_scope", "single") == "single"
                                    )
                                    or row.get("borrower_id") == params.get("borrower_id")
                                )
                            ),
                            None,
                        )
                        if duplicate is not None:
                            if "ON CONFLICT" in sql:
                                self._last = None
                                return
                            raise RuntimeError("duplicate lead_assignments idempotency key")
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
                        "request_id": request_id,
                        "assignment_scope": assignment_scope,
                        "loan_officer_id": params.get("loan_officer_id"),
                        "status": params.get("status") or "assigned",
                        "status_updated_at": now,
                    }
                    client.assignments.append(row)
                    self._last = client._loan_officer_assignment_row(row)
                elif "MAX(attempt_number)" in sql:
                    borrower_id = params.get("borrower_id")
                    attempts = [r["attempt_number"] for r in client.dispositions if r["borrower_id"] == borrower_id]
                    self._last = {"next_attempt": (max(attempts) if attempts else 0) + 1}
                elif "INSERT INTO mip_app.call_dispositions" in sql:
                    request_id = params.get("request_id")
                    if request_id:
                        duplicate = next(
                            (row for row in client.dispositions if row.get("request_id") == request_id),
                            None,
                        )
                        if duplicate is not None:
                            if "ON CONFLICT" in sql:
                                self._last = None
                                return
                            raise RuntimeError("duplicate call_dispositions.request_id")
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
                elif "INSERT INTO mip_app.lead_outcomes" in sql:
                    request_id = params.get("request_id")
                    source_system = params.get("source_system")
                    source_record_ref = params.get("source_record_ref")
                    duplicate = next(
                        (
                            row
                            for row in client.outcomes
                            if (request_id and row.get("request_id") == request_id)
                            or (
                                source_record_ref
                                and row.get("source_system") == source_system
                                and row.get("source_record_ref") == source_record_ref
                            )
                        ),
                        None,
                    )
                    if duplicate is not None:
                        if "ON CONFLICT" in sql:
                            self._last = None
                            return
                        raise RuntimeError("duplicate lead_outcomes idempotency key")
                    row = {
                        "outcome_id": uuid4(),
                        "borrower_id": params["borrower_id"],
                        "outcome_type": params["outcome_type"],
                        "source_system": params["source_system"],
                        "source_record_ref": params.get("source_record_ref"),
                        "assigned_to_email": params.get("assigned_to_email"),
                        "campaign_id": params.get("campaign_id"),
                        "loan_amount": params.get("loan_amount"),
                        "competitor_lender_label": params.get("competitor_lender_label"),
                        "occurred_at": params.get("occurred_at") or now,
                        "request_id": params.get("request_id"),
                        "audit_event_id": None,
                        "created_at": now,
                    }
                    client.outcomes.append(row)
                    self._last = row
                elif "INSERT INTO mip_app.feedback" in sql:
                    request_id = params.get("request_id")
                    assignment_id = params.get("assignment_id")
                    event_type = str(params.get("event_type") or "")
                    if request_id and any(
                        row.get("request_id") == request_id for row in client.feedback
                    ):
                        raise RuntimeError("duplicate feedback.request_id")
                    if (
                        assignment_id
                        and event_type.startswith("assignment_outcome_")
                        and any(
                            str(row.get("assignment_id")) == str(assignment_id)
                            and str(row.get("event_type") or "").startswith("assignment_outcome_")
                            for row in client.feedback
                        )
                    ):
                        raise RuntimeError("duplicate assignment outcome for assignment_id")
                    row = {
                        "feedback_id": uuid4(),
                        "borrower_id": params.get("borrower_id"),
                        "event_type": event_type,
                        "rating": params.get("rating"),
                        "comment": params.get("comment"),
                        "actor_email": params.get("actor_email"),
                        "assignment_id": assignment_id,
                        "request_id": request_id,
                        "audit_event_id": None,
                        "recorded_at": now,
                    }
                    client.feedback.append(row)
                    self._last = row
                elif "UPDATE mip_app.feedback" in sql:
                    for row in client.feedback:
                        if str(row["feedback_id"]) == str(params.get("feedback_id")):
                            row["audit_event_id"] = params.get("audit_event_id")
                    self._last = None
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
                elif "UPDATE mip_app.lead_outcomes" in sql:
                    for row in client.outcomes:
                        if str(row["outcome_id"]) == str(params.get("outcome_id")):
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


class _FakeHeadlineSqlClient:
    """Test-only warehouse stub for the KPI delta service's live headline
    read. Returns an empty row -> all-zero headline aggregates."""

    def __init__(self) -> None:
        self.statements: list[str] = []

    def execute_one(self, statement: str, parameters: Any = None) -> dict[str, Any] | None:
        self.statements.append(statement)
        return {}


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
    _reset_kpi_delta_service_for_tests()
    _reset_home_summary_service_for_tests()


def _reset_fake_dependency_state_for_tests() -> None:
    clear_sales_state_cache()
    factory = _BASE_DEPENDENCY_OVERRIDES.get(get_lakebase_client)
    if factory is None:
        return
    client = factory()
    reset = getattr(client, "reset_for_test", None)
    if callable(reset):
        reset()


@pytest.fixture
def fake_lakebase_client() -> _FakeLakebaseClient:
    """Return the session-bound ``_FakeLakebaseClient`` for introspection.

    Tests that need to assert what the approve/reject path wrote (e.g. the
    Feature C assignment + follow-up columns) can read ``.approvals`` etc.
    Per-test state is reset by ``_isolate_fastapi_dependency_state``.
    """
    factory = _BASE_DEPENDENCY_OVERRIDES.get(get_lakebase_client)
    assert factory is not None, "lakebase override not installed"
    return factory()


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
    geo_overlay = InProcessMockGeoOverlayService()
    audit = InMemoryAuditStore()
    lakebase = _FakeLakebaseClient()
    workspace = InMemoryWorkspaceStore()
    admin_rules = AdminRulesService(_FakeAdminSqlClient())
    asset_metadata = AssetMetadataService(_FakeAssetSqlClient())
    # Home summary: real service logic over the shared fake Lakebase (no
    # visit rows -> first-visit path) and a zero-row headline SQL stub;
    # spawn is a no-op so generic route tests can never fire a Genie turn.
    home_summary = HomeSummaryService(
        delta_service=KpiDeltaService(
            lakebase_client=lakebase, sql_client=_FakeHeadlineSqlClient()
        ),
        spawn=lambda work: None,
    )

    app.dependency_overrides[get_portfolio_repository] = lambda: portfolio
    app.dependency_overrides[get_analytics_repository] = lambda: analytics
    app.dependency_overrides[get_segment_repository] = lambda: segment
    app.dependency_overrides[get_lead_repository] = lambda: lead
    app.dependency_overrides[get_borrower_repository] = lambda: borrower
    app.dependency_overrides[get_offer_repository] = lambda: offer
    app.dependency_overrides[get_outreach_repository] = lambda: outreach
    app.dependency_overrides[get_genie_answer_repository] = lambda: genie
    app.dependency_overrides[get_geo_repository] = lambda: geo
    app.dependency_overrides[get_geo_assignment_overlay_service] = lambda: geo_overlay
    app.dependency_overrides[get_audit_store] = lambda: audit
    app.dependency_overrides[get_lakebase_client] = lambda: lakebase
    app.dependency_overrides[get_workspace_store] = lambda: workspace
    app.dependency_overrides[get_admin_rules_service] = lambda: admin_rules
    app.dependency_overrides[get_asset_metadata_service] = lambda: asset_metadata
    app.dependency_overrides[get_home_summary_service] = lambda: home_summary
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


@pytest.fixture(autouse=True)
def _disable_outreach_lifecycle_sync_for_unit_routes(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Keep route-level unit tests hermetic when approval endpoints enqueue sync.

    The lifecycle trigger implementation has dedicated coverage in
    ``tests/unit/test_job_trigger.py``. Unit tests that exercise approve/reject
    through ``TestClient`` should not reach the Databricks warehouse from a
    background task, because Starlette waits for that task before returning.
    Individual tests can still monkeypatch ``backend.api.outreach`` again when
    they need to assert enqueue behavior.
    """
    test_path = str(request.node.path)
    if test_path.endswith("tests/unit/test_job_trigger.py"):
        return

    from backend.api import outreach as outreach_mod

    monkeypatch.setattr(
        outreach_mod,
        "enqueue_lifecycle_trigger",
        lambda background, *, reason="approval": None,
    )


@pytest.fixture(autouse=True)
def _disable_live_supervisor_client_for_test_routes(
    monkeypatch: pytest.MonkeyPatch,
    request: pytest.FixtureRequest,
) -> None:
    """Prevent hermetic route tests from discovering local workspace auth.

    Supervisor request/response handling has focused tests that inject a serving
    client explicitly. Generic unit and in-process integration routes should
    exercise the governed fallback and must never construct a real
    ``WorkspaceClient`` just because a developer has Databricks credentials on
    disk. Explicitly enabled live integration runs retain production wiring.
    """
    test_path = str(request.node.path)
    if "tests/integration/" in test_path and os.environ.get("LAKEBASE_INTEGRATION") == "1":
        return

    from backend.services import (
        campaign_intelligence,
        growth_agent_composer,
        growth_agent_notification_intelligence,
        outreach_intelligence,
    )

    def _unit_workspace_client() -> object:
        raise RuntimeError("live Supervisor client disabled in unit tests")

    monkeypatch.setattr(
        outreach_intelligence,
        "workspace_client",
        _unit_workspace_client,
    )
    monkeypatch.setattr(
        campaign_intelligence,
        "workspace_client",
        _unit_workspace_client,
    )
    monkeypatch.setattr(
        growth_agent_notification_intelligence,
        "workspace_client",
        _unit_workspace_client,
    )
    monkeypatch.setattr(
        growth_agent_composer,
        "make_workspace_client",
        _unit_workspace_client,
    )
