"""Pin the timezone contract on ``PortfolioPreview.data_refreshed_at``.

Hole-finder round 2 #4 (2026-04-23): the Databricks SQL connector returns
TIMESTAMP as a tz-naive ``datetime``. Serialised to the wire without an
offset, ``new Date(...)`` in the browser re-interprets the string as
local time — European viewers saw the refresh stamp in the wrong hour.
The repository now stamps UTC (``+00:00``) on the outbound value; these
tests are the tripwire so a future refactor can't silently drop the
offset and bring the drift back.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta, timezone
from typing import Any

import pytest
from fastapi import HTTPException

from backend.schemas.campaign_status import authorize_campaign_status_transition
from backend.schemas.portfolio import (
    CampaignStatusPatchRequest,
    PortfolioCreateRequest,
    PortfolioCriteria,
    PortfolioPreviewRequest,
)
from backend.services.campaign_intelligence import (
    campaign_copy_hash,
    campaign_criteria_fingerprint,
    issue_campaign_variant_provenance,
)
from backend.services.lakebase import LakebaseError
from backend.services.repositories.databricks_portfolio import campaign_summary_from_row
from backend.services.repositories.databricks_repo import (
    DatabricksPortfolioRepository,
)


class _StubClient:
    """Minimal DatabricksSqlClient stand-in.

    Returns ``_preview_row`` for the aggregate query, ``_trend_rows`` for
    the funnel trend query, and ``_day_zero_row`` for the
    ``lead_population`` COUNT probe. We identify which query is in
    flight by looking for distinctive strings in the SQL text.
    """

    def __init__(
        self,
        preview_row: dict[str, Any],
        trend_rows: list[dict[str, Any]],
        day_zero_row: dict[str, Any] | None = None,
        workflow_row: dict[str, Any] | None = None,
    ):
        self._preview_row = preview_row
        self._trend_rows = trend_rows
        self._day_zero_row = day_zero_row if day_zero_row is not None else {"day_zero": False}
        self._workflow_row = (
            workflow_row
            if workflow_row is not None
            else {
                "approved_count": 0,
                "in_outreach_count": 0,
            }
        )
        self.preview_calls: int = 0
        self.statements: list[str] = []
        self.parameters: list[dict[str, Any] | None] = []

    def _is_day_zero_sql(self, sql: str) -> bool:
        return "lead_population" in sql and "day_zero" in sql

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> list[dict[str, Any]]:
        self.statements.append(sql)
        self.parameters.append(params)
        if "funnel_snapshot_daily" in sql:
            return self._trend_rows
        if "borrower_lifecycle_state" in sql:
            return [self._workflow_row]
        if self._is_day_zero_sql(sql):
            return [self._day_zero_row]
        return [self._preview_row]

    def execute_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.statements.append(sql)
        self.parameters.append(params)
        if "funnel_snapshot_daily" in sql:
            return self._trend_rows[0] if self._trend_rows else {}
        if "borrower_lifecycle_state" in sql:
            return self._workflow_row
        if self._is_day_zero_sql(sql):
            return self._day_zero_row
        self.preview_calls += 1
        return self._preview_row


class _StubLakebase:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        self.rows.append({"sql": sql, "params": params or {}})
        if "WITH inserted_campaign AS" not in sql and "FROM mip_app.campaigns c" in sql:
            return None
        return {
            "campaign_id": "11111111-1111-1111-1111-111111111111",
            "audit_id": "22222222-2222-2222-2222-222222222222",
            "request_payload_hash": (params or {}).get("request_payload_hash"),
            "creation_response": json.loads(str((params or {}).get("creation_response") or "{}")),
        }


class _IdempotentCampaignLakebase:
    """Stateful campaign store that models the production unique key."""

    def __init__(self) -> None:
        self.stored: dict[str, Any] | None = None
        self.insert_calls = 0
        self.lookup_calls = 0

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        values = params or {}
        if "WITH inserted_campaign AS" in sql:
            self.insert_calls += 1
            assert self.stored is None
            self.stored = {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "audit_id": "22222222-2222-4222-8222-222222222222",
                "request_payload_hash": values["request_payload_hash"],
                "creation_response": json.loads(str(values["creation_response"])),
                "created": True,
            }
            return dict(self.stored)
        if "FROM mip_app.campaigns c" in sql:
            self.lookup_calls += 1
            return dict(self.stored) if self.stored is not None else None
        raise AssertionError(f"unexpected Lakebase query: {sql[:80]}")


class _ConcurrentCampaignLakebase:
    """Model an insert loser whose winner becomes visible on a later SELECT."""

    def __init__(self, *, publish_after_lookup: int | None, mismatch: bool = False) -> None:
        self.publish_after_lookup = publish_after_lookup
        self.mismatch = mismatch
        self.stored: dict[str, Any] | None = None
        self.insert_calls = 0
        self.lookup_calls = 0

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any] | None:
        values = params or {}
        if "WITH inserted_campaign AS" in sql:
            self.insert_calls += 1
            assert "selected_campaign" not in sql
            payload_hash = str(values["request_payload_hash"])
            self.stored = {
                "campaign_id": "11111111-1111-4111-8111-111111111118",
                "audit_id": "22222222-2222-4222-8222-222222222228",
                "request_payload_hash": "different-hash" if self.mismatch else payload_hash,
                "creation_response": json.loads(str(values["creation_response"])),
            }
            return None
        if "FROM mip_app.campaigns c" in sql:
            self.lookup_calls += 1
            if (
                self.stored is not None
                and self.publish_after_lookup is not None
                and self.lookup_calls >= self.publish_after_lookup
            ):
                return dict(self.stored)
            return None
        raise AssertionError(f"unexpected Lakebase query: {sql[:80]}")


class _CampaignListLakebase:
    def __init__(self) -> None:
        self.calls: list[dict[str, object]] = []

    def fetchall(
        self,
        sql: str,
        params: dict[str, Any] | None = None,
        *,
        limit: int | None = None,
    ) -> list[dict[str, Any]]:
        self.calls.append({"sql": sql, "params": params or {}, "limit": limit})
        return [
            {
                "campaign_id": "11111111-1111-4111-8111-111111111111",
                "name": "Maya QA CA recapture",
                "owner_email": "skyler@entrada.ai",
                "status": "draft",
                "criteria": {},
                "suppression_policy": {},
                "message_variants": [],
                "channel_cascade": [],
                "send_window": {},
                "holdout": None,
                "roi_assumptions": None,
                "created_at": datetime(2026, 5, 12, 12, 0, tzinfo=UTC),
                "updated_at": datetime(2026, 5, 12, 12, 1, tzinfo=UTC),
            }
        ]


class _CampaignPatchLakebase:
    def __init__(
        self,
        *,
        suppression_policy: dict[str, object],
        current_status: str = "draft",
    ) -> None:
        self.suppression_policy = suppression_policy
        self.current_status = current_status
        self.calls: list[dict[str, object]] = []

    def _row(self, *, status: str = "draft") -> dict[str, object]:
        return {
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "name": "Maya QA CA recapture",
            "owner_email": "skyler@entrada.ai",
            "status": status,
            "criteria": {"marketing_eligibility": "Eligible only"},
            "suppression_policy": self.suppression_policy,
            "message_variants": [],
            "channel_cascade": [],
            "send_window": {},
            "holdout": None,
            "roi_assumptions": None,
        }

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.calls.append({"sql": sql, "params": params or {}})
        if "JOIN mip_app.action_audit" in sql:
            return None  # type: ignore[return-value]
        if "UPDATE mip_app.campaigns" in sql:
            return {
                **self._row(status=str((params or {}).get("status") or "pending_review")),
                "audit_id": "22222222-2222-4222-8222-222222222222",
            }  # type: ignore[return-value]
        return self._row(status=self.current_status)  # type: ignore[return-value]

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.calls.append({"sql": sql, "params": params or {}})


def _preview_row(data_refreshed_at: Any = None) -> dict[str, Any]:
    return {
        "marketable_population": 1000,
        "high_intent_leads": 300,
        "top_tier_opportunities": 120,
        "offers_recommended": 250,
        "avg_score": 72,
        "data_refreshed_at": data_refreshed_at,
    }


def _server_provenance(
    subject: str,
    body: str,
    *,
    criteria: PortfolioCriteria | None = None,
) -> dict[str, str]:
    label = "Agent endpoint-generated recommendation"
    return {
        "generation_mode": "supervisor",
        "generator_label": label,
        "provenance_token": issue_campaign_variant_provenance(
            generation_mode="supervisor",
            generator_label=label,
            subject=subject,
            body=body,
            criteria_fingerprint=campaign_criteria_fingerprint(criteria or PortfolioCriteria()),
        ),
    }


def _trend_row(
    snapshot_at: Any,
    *,
    snapshot_date: str = "2026-04-22",
    top_tier_opportunities: int = 120,
    avg_score: int = 72,
) -> dict[str, Any]:
    return {
        "snapshot_date": snapshot_date,
        "snapshot_at": snapshot_at,
        "marketable_population": 1000,
        "high_intent_leads": 300,
        "top_tier_opportunities": top_tier_opportunities,
        "offers_recommended": 250,
        "avg_score": avg_score,
        "approved_count": 0,
        "in_outreach_count": 0,
    }


def test_preview_sql_projects_every_aggregate_input() -> None:
    template = DatabricksPortfolioRepository._PREVIEW_SQL_TEMPLATE
    projection, aggregate = template.split(") SELECT ", maxsplit=1)

    for column in (
        "in_the_money",
        "is_high_opportunity",
        "offer_recommended",
        "offer_available",
        "opportunity_score",
        "current_lien_balance",
        "equity_pct",
        "refreshed_at",
        "recommended_offer_code",
    ):
        assert f"headline.{column}" in projection
    assert "borrower.rate_spread_bps AS preview_rate_spread_bps" in projection
    assert "AVG(preview_rate_spread_bps)" in aggregate
    assert "AVG(rate_spread_bps)" not in aggregate
    assert "FROM preview_population" in aggregate


def test_naive_datetime_is_stamped_as_utc():
    """Tz-naive datetimes from the connector must come out as tz-aware UTC."""
    naive = datetime(2026, 4, 22, 18, 30, 0)
    client = _StubClient(_preview_row(naive), [_trend_row(naive)])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is not None
    assert (
        preview.data_refreshed_at.tzinfo is not None
    ), "tz-naive datetime leaked through — browser will interpret as local time"
    assert preview.data_refreshed_at.utcoffset() == UTC.utcoffset(preview.data_refreshed_at)
    # Serialised form must carry a UTC marker — either 'Z' (Pydantic's
    # default for UTC-aware datetimes) or '+00:00'. A bare
    # 'YYYY-MM-DDTHH:MM:SS' would mean the naive value leaked through.
    serialised = preview.model_dump_json()
    assert (
        '"data_refreshed_at":"2026-04-22T18:30:00Z"' in serialised
        or '"data_refreshed_at":"2026-04-22T18:30:00+00:00"' in serialised
    ), serialised


def test_already_aware_datetime_is_converted_to_utc():
    """A tz-aware datetime in another zone should convert to UTC, not stay
    in its source zone (otherwise clients comparing offsets drift)."""
    # Fixed UTC-5 (no DST) so the assertion below is deterministic.
    minus_five = timezone(-timedelta(hours=5))
    aware = datetime(2026, 4, 22, 13, 30, 0, tzinfo=minus_five)
    client = _StubClient(_preview_row(aware), [_trend_row(aware)])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is not None
    # 13:30 UTC-5 == 18:30 UTC
    assert preview.data_refreshed_at.hour == 18
    assert preview.data_refreshed_at.utcoffset() == UTC.utcoffset(preview.data_refreshed_at)


def test_iso_string_is_parsed_and_stamped_utc():
    """Defensive: a future connector change could emit an ISO string. The
    repository parses it and stamps UTC rather than passing through a raw
    string that Pydantic then serialises without tz."""
    client = _StubClient(
        _preview_row("2026-04-22T18:30:00"),
        [_trend_row("2026-04-22T18:30:00")],
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is not None
    assert preview.data_refreshed_at.tzinfo is not None


def test_none_stays_none():
    """Missing snapshot (Day-0 empty gold tables) must keep ``None`` — the
    UI renders the Day-0 empty-state banner on that exact shape."""
    client = _StubClient(_preview_row(), [])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is None


def test_preview_maps_every_offer_mix_column_without_collapsing_categories() -> None:
    row = _preview_row()
    expected = {
        "purchase": 11,
        "refi_plus_heloc": 22,
        "heloc": 33,
        "refi": 44,
        "cash_out": 55,
        "investor": 66,
        "retention": 77,
        "nurture": 88,
    }
    row.update({f"offer_{code}": count for code, count in expected.items()})
    client = _StubClient(row, [])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert {offer.offer_code: offer.borrower_count for offer in preview.offer_mix} == expected


@pytest.mark.parametrize("bad_value", ["not a date", object(), 12345])
def test_unparseable_values_fall_back_to_none(bad_value: Any):
    """Defence in depth: a malformed value should coerce to ``None`` so a
    broken row upstream doesn't poison the whole preview response."""
    client = _StubClient(_preview_row(), [_trend_row(bad_value)])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    # Unparseable → None. Anything else (including a raw string echoed back)
    # would mean the timezone fix regressed.
    assert preview.data_refreshed_at is None or preview.data_refreshed_at.tzinfo is not None


# ---------------------------------------------------------------------------
# R5-20 -- server-authoritative ``day_zero`` flag on PortfolioPreview
# ---------------------------------------------------------------------------


def test_day_zero_true_when_lead_population_empty():
    """Authoritative signal: ``COUNT(*) FROM mip.gold.lead_population == 0``.

    Frontend used to infer day-0 from ``marketable_population == 0 AND
    data_refreshed_at IS NULL``. That mis-classifies a partial CTAS
    roll -- e.g. borrower_360 is being rewritten and briefly holds a
    non-zero count while the snapshot row is still null. The server flag
    closes that window.
    """
    client = _StubClient(
        _preview_row(),
        [_trend_row("2026-04-22T18:30:00")],
        day_zero_row={"day_zero": True},
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    preview = repo.preview(None)
    assert preview.day_zero is True


def test_day_zero_false_when_lead_population_has_rows():
    client = _StubClient(
        _preview_row(),
        [_trend_row("2026-04-22T18:30:00")],
        day_zero_row={"day_zero": False},
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    preview = repo.preview(None)
    assert preview.day_zero is False


def test_day_zero_probe_failure_propagates():
    """R6-07: a warehouse failure on the day-zero probe must propagate.

    The prior implementation silently returned ``False`` on exception,
    which surfaced a misleading preview -- a degraded banner on top of
    a "there IS data, it's just 0" KPI grid. Now the exception bubbles
    to the router so the caller sees one honest 503 and the warming-up
    banner instead of a half-rendered page.
    """

    class _FailingClient(_StubClient):
        def execute_one(
            self,
            sql: str,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any]:
            if self._is_day_zero_sql(sql):
                raise RuntimeError("warehouse probe failed")
            return super().execute_one(sql, params)

    client = _FailingClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    with pytest.raises(RuntimeError, match="warehouse probe failed"):
        repo.preview(None)


# ---------------------------------------------------------------------------
# R5-08 -- deterministic cache key independent of dict iteration order
# ---------------------------------------------------------------------------


def test_preview_cache_key_stable_across_param_order():
    """``{'a':1,'b':2}`` and ``{'b':2,'a':1}`` must produce the same key."""
    k1 = DatabricksPortfolioRepository._preview_cache_key("WHERE a=:a AND b=:b", {"a": 1, "b": 2})
    k2 = DatabricksPortfolioRepository._preview_cache_key("WHERE a=:a AND b=:b", {"b": 2, "a": 1})
    assert k1 == k2, f"cache key drifts with dict order: {k1!r} != {k2!r}"


def test_preview_cache_key_distinguishes_distinct_criteria():
    """Different WHERE clauses and/or values MUST hash to different keys
    (we'd rather miss cache than serve the wrong row)."""
    k1 = DatabricksPortfolioRepository._preview_cache_key("WHERE state=:s", {"s": "IL"})
    k2 = DatabricksPortfolioRepository._preview_cache_key("WHERE state=:s", {"s": "CA"})
    k3 = DatabricksPortfolioRepository._preview_cache_key("", {})
    assert k1 != k2 != k3 and k1 != k3


def test_preview_second_call_same_order_hits_cache():
    """Functional: two calls with semantically-equivalent criteria must
    share the same cache entry. The second call should NOT re-run the
    preview SELECT on the stub client."""
    client = _StubClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    req = PortfolioPreviewRequest(criteria=PortfolioCriteria(min_equity_pct=25))
    repo.preview(req)
    calls_after_first = client.preview_calls
    repo.preview(req)
    calls_after_second = client.preview_calls
    assert (
        calls_after_first == calls_after_second == 1
    ), f"second call should have hit cache; saw {calls_after_second} preview SELECTs"


def test_campaign_list_is_fresh_lakebase_state_not_preview_cache(monkeypatch):
    """GET /api/portfolio is the campaign list, not the KPI preview cache.

    Resilience audits should benchmark POST /api/portfolio/preview for
    aggregate cache behavior. Campaign list reads are mutation-adjacent
    Lakebase state, so each call intentionally reaches Lakebase.
    """
    lakebase = _CampaignListLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(
        _StubClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    )  # type: ignore[arg-type]

    first = repo.list_campaigns(owner_email="skyler@entrada.ai")
    second = repo.list_campaigns(owner_email="skyler@entrada.ai")

    assert len(first.campaigns) == len(second.campaigns) == 1
    assert len(lakebase.calls) == 2
    assert all("mip_app.campaigns" in str(call["sql"]) for call in lakebase.calls)


def test_campaign_summary_derives_creation_verification_from_durable_proof() -> None:
    generated = {
        "variant_name": "A",
        "generation_mode": "supervisor",
        "generator_label": "Databricks Agent Responses",
        "provenance_key_id": "v1",
        "subject": "Review your options",
        "body": "A loan officer can review the available options.",
        "provenance_copy_hash": campaign_copy_hash(
            "Review your options",
            "A loan officer can review the available options.",
        ),
        "provenance_criteria_fingerprint": campaign_criteria_fingerprint({}),
        "provenance_issued_at": "2026-07-14T00:00:00Z",
        "provenance_expires_at": "2026-07-15T00:00:00Z",
    }
    operator = {
        "variant_name": "B",
        "generation_mode": "operator",
        "generator_label": "Operator edited",
    }

    summary = campaign_summary_from_row(
        {
            "campaign_id": "11111111-1111-4111-8111-111111111111",
            "name": "Verified campaign",
            "owner_email": "skyler@entrada.ai",
            "status": "draft",
            "criteria": {},
            "message_variants": [generated, operator],
        }
    )

    assert summary.message_variants[0]["copy_verified_at_creation"] is True
    assert summary.message_variants[1]["copy_verified_at_creation"] is False
    assert "provenance_token" not in summary.message_variants[0]


def test_campaign_list_excludes_archived_by_default(monkeypatch):
    """Re-audit #4 (2026-06-12): 'archived' is the hide-from-product status,
    but the default listing returned every status — and a PATCH-to-archived
    sets updated_at=now(), so archiving dev detritus (load-test + Genie-draft
    rows) would have bumped it to the TOP of Saved Campaigns instead of
    removing it. Default listing must exclude archived; an explicit
    status='archived' query must still return them."""
    lakebase = _CampaignListLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(
        _StubClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    )  # type: ignore[arg-type]

    repo.list_campaigns(owner_email="skyler@entrada.ai")
    default_sql = str(lakebase.calls[-1]["sql"])
    # The default (status=None) branch hides archived; the SQL carries the
    # exclusion so the regression can't be silently removed.
    assert "status <> 'archived'" in default_sql
    assert lakebase.calls[-1]["params"]["status"] is None

    repo.list_campaigns(owner_email="skyler@entrada.ai", status="archived")
    # Explicit status is passed through unchanged (audit/admin can still see
    # archived rows by asking for them).
    assert lakebase.calls[-1]["params"]["status"] == "archived"


def test_trend_delta_uses_exact_snapshot_date_and_drops_bootstrap_zero():
    """A bootstrap 0 row must not become a fake percent change baseline."""
    trend_rows = [
        _trend_row(
            "2026-05-04T19:48:14",
            snapshot_date="2026-05-04",
            top_tier_opportunities=3074,
        ),
        _trend_row(
            "2026-04-23T19:48:14",
            snapshot_date="2026-04-23",
            top_tier_opportunities=3081,
        ),
        _trend_row(
            "2026-04-22T19:48:14",
            snapshot_date="2026-04-22",
            top_tier_opportunities=0,
        ),
    ]
    client = _StubClient(_preview_row(), trend_rows)
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)
    trend = preview.trends["top_tier_opportunities"]

    assert trend.series == [3081.0, 3074.0]
    assert trend.comparison_label == "vs 2026-04-23"
    assert trend.delta_pct == -0.2
    assert (
        trend.note
        == "Comparison starts on 2026-04-23 because earlier snapshots predate this metric."
    )


def test_trend_step_change_adds_source_backed_context_note():
    trend_rows = [
        _trend_row(
            "2026-05-08T19:48:14",
            snapshot_date="2026-05-08",
            top_tier_opportunities=4320,
        ),
        _trend_row(
            "2026-05-07T19:48:14",
            snapshot_date="2026-05-07",
            top_tier_opportunities=4542,
        ),
        _trend_row(
            "2026-05-06T19:48:14",
            snapshot_date="2026-05-06",
            top_tier_opportunities=3074,
        ),
    ]
    client = _StubClient(_preview_row(), trend_rows)
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)
    trend = preview.trends["top_tier_opportunities"]

    assert trend.note is not None
    assert "coverage or rules update on 2026-05-07" in trend.note
    assert "counts remain source-backed" in trend.note
    assert "verify rules" not in trend.note


def test_filtered_preview_suppresses_national_trends():
    """Filtered KPIs must not reuse the _ALL/_ALL national trend line."""
    client = _StubClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    req = PortfolioPreviewRequest(criteria=PortfolioCriteria(min_equity_pct=25))

    preview = repo.preview(req)

    assert preview.trends == {}
    assert preview.trend_status == "not_applicable"
    assert preview.trend_note is not None
    assert preview.approved_count is None
    assert preview.in_outreach_count is None


def test_unfiltered_preview_uses_live_workflow_counts_over_snapshot():
    """Workflow counts should match current lifecycle state, not stale daily snapshots."""
    trend = _trend_row("2026-04-22T18:30:00")
    trend["approved_count"] = 23
    trend["in_outreach_count"] = 0
    client = _StubClient(
        _preview_row(),
        [trend],
        workflow_row={"approved_count": 23, "in_outreach_count": 1},
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.approved_count == 23
    assert preview.in_outreach_count == 1
    workflow_sql = next(sql for sql in client.statements if "borrower_lifecycle_state" in sql)
    assert "mip.gold.borrower_360" in workflow_sql


def test_create_uses_submitted_criteria_for_population_count(monkeypatch):
    """Saved portfolio size must match the reviewed criteria, not the national default."""
    client = _StubClient(_preview_row(), [])
    lakebase = _StubLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    repo.create(
        PortfolioCreateRequest(
            name="Owner occupied",
            criteria=PortfolioCriteria(occupancy="Owner-occupied", min_equity_pct_label="≥ 25%"),
        ),
        idempotency_key="11111111-1111-4111-8111-111111111113",
    )

    preview_index = next(
        i for i, sql in enumerate(client.statements) if "portfolio_headline_metric_view" in sql
    )
    preview_sql = client.statements[preview_index]
    preview_params = client.parameters[preview_index]
    assert "is_owner_occupied = TRUE" in preview_sql
    assert "equity_pct >= :equity_floor" in preview_sql
    assert "marketing_eligible = TRUE" in preview_sql
    assert preview_params == {"equity_floor": 25}
    assert lakebase.rows
    write = next(row for row in lakebase.rows if "INSERT INTO mip_app.campaigns" in str(row["sql"]))
    assert "mip_app.action_audit" in write["sql"]
    assert write["params"]["criteria"] == (
        '{"occupancy": "Owner-occupied", "marketing_eligibility": "Eligible only", '
        '"min_equity_pct_label": "\\u2265 25%"}'
    )
    metadata = json.loads(str(write["params"]["metadata"]))
    assert metadata["source"] == "portfolio_builder"
    assert metadata["portfolio_criteria"] == {
        "occupancy": "Owner-occupied",
        "marketing_eligibility": "Eligible only",
        "min_equity_pct_label": "≥ 25%",
    }
    assert "criteria" not in metadata
    assert metadata["action"] == "portfolio.create"
    assert metadata["marketable_population"] == 1000


def test_create_commits_message_variants_and_generation_provenance_atomically(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _StubLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    subject_a = "Review your current mortgage options"
    body_a = (
        "A loan officer can explain the available options and tradeoffs. "
        "Would you like to schedule a review?"
    )
    subject_b = "A clearer mortgage review"
    body_b = (
        "Compare your current mortgage with other available options. "
        "Would a review with a loan officer be useful?"
    )

    repo.create(
        PortfolioCreateRequest(
            name="Illinois refinance review",
            message_variants=[
                {
                    "variant_name": "A",
                    "channel": "email",
                    "subject": subject_a,
                    "body": body_a,
                    "weight_pct": 45,
                    **_server_provenance(subject_a, body_a),
                },
                {
                    "variant_name": "B",
                    "channel": "email",
                    "subject": subject_b,
                    "body": body_b,
                    "weight_pct": 45,
                    **_server_provenance(subject_b, body_b),
                },
            ],
        ),
        idempotency_key="11111111-1111-4111-8111-111111111114",
    )

    assert len(lakebase.rows) == 2
    write = next(row for row in lakebase.rows if "INSERT INTO mip_app.campaigns" in str(row["sql"]))
    sql = str(write["sql"])
    assert "WITH inserted_campaign AS" in sql
    assert "selected_campaign" not in sql
    assert "FROM mip_app.campaigns c" not in sql
    assert "inserted_audit AS" in sql
    assert "inserted_variants AS" in sql
    assert "jsonb_to_recordset(%(variant_rows)s::jsonb)" in sql
    params = write["params"]
    assert isinstance(params, dict)
    variants = json.loads(str(params["variant_rows"]))
    assert [variant["variant_name"] for variant in variants] == ["A", "B"]
    assert {variant["generation_mode"] for variant in variants} == {"supervisor"}
    assert {variant["generator_label"] for variant in variants} == {
        "Agent endpoint-generated recommendation"
    }
    assert all(variant["provenance_key_id"] == "v1" for variant in variants)
    assert all(len(variant["provenance_copy_hash"]) == 64 for variant in variants)
    assert all(len(variant["provenance_criteria_fingerprint"]) == 64 for variant in variants)
    assert all(variant["provenance_performance_fingerprint"] is None for variant in variants)
    assert all(len(variant["provenance_token_digest"]) == 64 for variant in variants)
    assert all(variant["provenance_issued_at"] for variant in variants)
    assert all(variant["provenance_expires_at"] for variant in variants)
    metadata = json.loads(str(params["metadata"]))
    assert metadata["campaign_generation_mode"] == "supervisor"
    assert metadata["generator_label"] == "Agent endpoint-generated recommendation"


def test_create_preserves_mixed_generation_provenance_per_variant(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _StubLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    generated_subject = "Review your mortgage options"
    generated_body = "Compare your current mortgage options with a loan officer."

    repo.create(
        PortfolioCreateRequest(
            name="Reviewed mixed campaign",
            message_variants=[
                {
                    "variant_name": "A",
                    "channel": "email",
                    "subject": generated_subject,
                    "body": generated_body,
                    **_server_provenance(generated_subject, generated_body),
                },
                {
                    "variant_name": "B",
                    "channel": "email",
                    "subject": "Schedule a mortgage review",
                    "body": "Schedule a review of the mortgage options available to you.",
                    "generation_mode": "operator",
                    "generator_label": "Operator edited",
                },
            ],
        ),
        idempotency_key="11111111-1111-4111-8111-111111111117",
    )

    write = next(row for row in lakebase.rows if "INSERT INTO mip_app.campaigns" in str(row["sql"]))
    metadata = json.loads(str(write["params"]["metadata"]))
    assert metadata["campaign_generation_mode"] == "mixed"
    assert metadata["generator_label"] == "Multiple generators"
    generated, operator = metadata["variant_provenance"]
    assert generated["generation_mode"] == "supervisor"
    assert generated["generator_label"] == "Agent endpoint-generated recommendation"
    assert generated["variant_name"] == "A"
    assert generated["provenance_key_id"] == "v1"
    assert len(generated["provenance_copy_hash"]) == 64
    assert len(generated["provenance_criteria_fingerprint"]) == 64
    assert generated["provenance_performance_fingerprint"] is None
    assert operator == {
        "generation_mode": "operator",
        "generator_label": "Operator edited",
        "variant_name": "B",
    }


def test_create_rejects_forged_or_copy_transplanted_generation_provenance(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _StubLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    subject = "Review your mortgage options"
    original_body = "Compare your current mortgage options with a loan officer."
    provenance = _server_provenance(subject, original_body)

    with pytest.raises(ValueError, match="provenance is missing, expired, or invalid"):
        repo.create(
            PortfolioCreateRequest(
                name="Forged provenance review",
                message_variants=[
                    {
                        "variant_name": "Primary",
                        "channel": "email",
                        "subject": subject,
                        "body": "Schedule a different mortgage review with a loan officer.",
                        **provenance,
                    }
                ],
            ),
            idempotency_key="11111111-1111-4111-8111-111111111121",
        )

    with pytest.raises(ValueError, match="provenance is missing, expired, or invalid"):
        repo.create(
            PortfolioCreateRequest(
                name="Unsigned provenance review",
                message_variants=[
                    {
                        "variant_name": "Primary",
                        "channel": "email",
                        "subject": subject,
                        "body": original_body,
                        "generation_mode": "supervisor",
                        "generator_label": "Agent endpoint-generated recommendation",
                    }
                ],
            ),
            idempotency_key="11111111-1111-4111-8111-111111111122",
        )

    with pytest.raises(ValueError, match="provenance is missing, expired, or invalid"):
        repo.create(
            PortfolioCreateRequest(
                name="Cohort transplant review",
                criteria=PortfolioCriteria(),
                message_variants=[
                    {
                        "variant_name": "Primary",
                        "channel": "email",
                        "subject": subject,
                        "body": original_body,
                        **_server_provenance(
                            subject,
                            original_body,
                            criteria=PortfolioCriteria(states=["IL"]),
                        ),
                    }
                ],
            ),
            idempotency_key="11111111-1111-4111-8111-111111111123",
        )


def test_create_replays_same_request_without_second_campaign_or_audit(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _IdempotentCampaignLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    payload = PortfolioCreateRequest(name="Illinois refinance review")
    key = "11111111-1111-4111-8111-111111111115"

    first = repo.create(payload, actor="manager@example.com", idempotency_key=key)
    preview_calls_after_first = client.preview_calls
    second = repo.create(payload, actor="manager@example.com", idempotency_key=key)

    assert second == first
    assert lakebase.insert_calls == 1
    assert lakebase.lookup_calls == 2
    assert client.preview_calls == preview_calls_after_first


def test_create_rejects_same_request_for_different_campaign_without_write(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _IdempotentCampaignLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]
    key = "11111111-1111-4111-8111-111111111116"
    repo.create(
        PortfolioCreateRequest(name="Illinois refinance review"),
        actor="manager@example.com",
        idempotency_key=key,
    )
    preview_calls_after_first = client.preview_calls

    with pytest.raises(ValueError, match="different campaign payload"):
        repo.create(
            PortfolioCreateRequest(name="Texas refinance review"),
            actor="manager@example.com",
            idempotency_key=key,
        )

    assert lakebase.insert_calls == 1
    assert client.preview_calls == preview_calls_after_first


def test_campaign_replay_treats_malformed_stored_response_as_dependency_failure():
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]

    with pytest.raises(LakebaseError, match="idempotency record is invalid"):
        repo._campaign_create_response_from_idempotency_row(
            {
                "request_payload_hash": "expected",
                "campaign_id": "11111111-1111-4111-8111-111111111115",
                "creation_response": {
                    "name": "Illinois refinance review",
                    "marketable_population": "not-a-number",
                    "household_summary": {},
                },
            },
            expected_payload_hash="expected",
        )


def test_create_resolves_concurrent_insert_with_bounded_separate_lookup(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _ConcurrentCampaignLakebase(publish_after_lookup=3)
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_portfolio.time.sleep",
        lambda _delay: None,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    result = repo.create(
        PortfolioCreateRequest(name="Illinois refinance review"),
        actor="manager@example.com",
        idempotency_key="11111111-1111-4111-8111-111111111118",
    )

    assert result.campaign_id == "11111111-1111-4111-8111-111111111118"
    assert lakebase.insert_calls == 1
    assert lakebase.lookup_calls == 3


def test_create_rejects_concurrent_idempotency_payload_mismatch(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _ConcurrentCampaignLakebase(publish_after_lookup=2, mismatch=True)
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="different campaign payload"):
        repo.create(
            PortfolioCreateRequest(name="Illinois refinance review"),
            actor="manager@example.com",
            idempotency_key="11111111-1111-4111-8111-111111111119",
        )


def test_create_bounds_empty_post_conflict_idempotency_lookup(monkeypatch):
    client = _StubClient(_preview_row(), [])
    lakebase = _ConcurrentCampaignLakebase(publish_after_lookup=None)
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_portfolio.time.sleep",
        lambda _delay: None,
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    with pytest.raises(LakebaseError, match="idempotency lookup was empty"):
        repo.create(
            PortfolioCreateRequest(name="Illinois refinance review"),
            actor="manager@example.com",
            idempotency_key="11111111-1111-4111-8111-111111111120",
        )

    assert lakebase.insert_calls == 1
    assert lakebase.lookup_calls == 4


def test_campaign_create_audit_lookup_uses_action_audit_event_at() -> None:
    sql = DatabricksPortfolioRepository._CAMPAIGN_IDEMPOTENCY_LOOKUP_SQL

    assert "ORDER BY a.event_at ASC" in sql
    assert "a.occurred_at" not in sql


def test_campaign_status_request_id_is_stable_and_payload_bound() -> None:
    campaign_id = "11111111-1111-4111-8111-111111111111"
    payload = CampaignStatusPatchRequest(status="pending_review", rationale="Reviewed cohort")

    first = DatabricksPortfolioRepository._campaign_status_request_id(
        campaign_id,
        payload,
        actor="Manager@Example.com",
    )
    second = DatabricksPortfolioRepository._campaign_status_request_id(
        campaign_id,
        payload,
        actor="manager@example.com",
    )
    changed = DatabricksPortfolioRepository._campaign_status_request_id(
        campaign_id,
        CampaignStatusPatchRequest(status="pending_review", rationale="Different review"),
        actor="manager@example.com",
    )
    next_cycle = DatabricksPortfolioRepository._campaign_status_request_id(
        campaign_id,
        payload,
        actor="manager@example.com",
        source_status="draft",
        source_updated_at="2026-07-14T12:05:00+00:00",
    )
    prior_cycle = DatabricksPortfolioRepository._campaign_status_request_id(
        campaign_id,
        payload,
        actor="manager@example.com",
        source_status="draft",
        source_updated_at="2026-07-14T12:00:00+00:00",
    )
    explicit_replay = DatabricksPortfolioRepository._campaign_status_request_id(
        campaign_id,
        CampaignStatusPatchRequest(status="draft", rationale="A later payload"),
        actor="manager@example.com",
        caller_request_id="campaign-cycle-request-1",
        source_status="pending_review",
        source_updated_at="2026-07-14T12:10:00+00:00",
    )
    explicit_original = DatabricksPortfolioRepository._campaign_status_request_id(
        campaign_id,
        payload,
        actor="manager@example.com",
        caller_request_id="campaign-cycle-request-1",
        source_status="draft",
        source_updated_at="2026-07-14T12:00:00+00:00",
    )

    assert first == second
    assert first.startswith("campaign-status-")
    assert len(first.removeprefix("campaign-status-")) == 64
    assert changed != first
    assert next_cycle != prior_cycle
    assert explicit_replay == explicit_original


def test_campaign_status_sql_binds_campaign_and_audit_to_one_transition_instance() -> None:
    patch_sql = DatabricksPortfolioRepository._CAMPAIGN_PATCH_SQL
    replay_sql = DatabricksPortfolioRepository._CAMPAIGN_STATUS_IDEMPOTENCY_LOOKUP_SQL

    assert "updated_at = %(transition_at)s::timestamptz" in patch_sql
    assert "metadata, event_at" in patch_sql
    assert "%(transition_at)s::timestamptz" in patch_sql
    assert "a.event_at AS updated_at" in replay_sql
    assert "a.metadata->>'status'" in replay_sql


def test_campaign_status_retry_replays_one_audited_transition(monkeypatch):
    class _ReplayStatusLakebase(_CampaignPatchLakebase):
        def __init__(self) -> None:
            super().__init__(suppression_policy={"default": "eligible_only"})
            self.audit_id = "22222222-2222-4222-8222-222222222222"
            self.request_id: str | None = None
            self.patch_count = 0
            self.audit_count = 0

        def fetchone(
            self,
            sql: str,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            values = params or {}
            self.calls.append({"sql": sql, "params": values})
            if "JOIN mip_app.action_audit" in sql:
                if self.request_id == values.get("request_id"):
                    return {
                        **self._row(status=self.current_status),
                        "audit_id": self.audit_id,
                    }
                return None
            if "UPDATE mip_app.campaigns" in sql:
                self.patch_count += 1
                if self.current_status != values.get("current_status"):
                    return None
                self.current_status = str(values["status"])
                self.request_id = str(values["request_id"])
                self.audit_count += 1
                return {
                    **self._row(status=self.current_status),
                    "audit_id": self.audit_id,
                }
            return self._row(status=self.current_status)  # type: ignore[return-value]

    lakebase = _ReplayStatusLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_portfolio.get_correlation_id",
        lambda: "campaign-status-request-1",
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]
    payload = CampaignStatusPatchRequest(status="pending_review")

    first = repo.patch_status(
        "11111111-1111-4111-8111-111111111111",
        payload,
        actor="manager@example.com",
    )
    second = repo.patch_status(
        "11111111-1111-4111-8111-111111111111",
        payload,
        actor="manager@example.com",
    )

    assert first == second
    assert lakebase.patch_count == 1
    assert lakebase.audit_count == 1


def test_campaign_status_allows_repeated_lifecycle_cycle_and_stable_old_replay(monkeypatch):
    class _LifecycleLakebase(_CampaignPatchLakebase):
        def __init__(self) -> None:
            super().__init__(
                suppression_policy={"default": "eligible_only"},
                current_status="draft",
            )
            self.updated_at = datetime(2026, 7, 14, 12, 0, tzinfo=UTC)
            self.audits: dict[str, dict[str, Any]] = {}

        def _current_row(self) -> dict[str, Any]:
            return {
                **self._row(status=self.current_status),
                "created_at": datetime(2026, 7, 14, 11, 0, tzinfo=UTC),
                "updated_at": self.updated_at,
            }

        def fetchone(
            self,
            sql: str,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            values = params or {}
            self.calls.append({"sql": sql, "params": values})
            if "JOIN mip_app.action_audit" in sql:
                return self.audits.get(str(values.get("request_id") or ""))
            if "UPDATE mip_app.campaigns" not in sql:
                return self._current_row()
            if self.current_status != values.get("current_status"):
                return None
            self.current_status = str(values["status"])
            self.updated_at = values["transition_at"]
            request_id = str(values["request_id"])
            audit_row = {
                **self._current_row(),
                "audit_id": f"audit-{len(self.audits) + 1}",
                "audit_metadata": json.loads(str(values["metadata"])),
            }
            self.audits[request_id] = audit_row
            return audit_row

    request_ids = iter(
        [
            "campaign-cycle-pending-1",
            "campaign-cycle-pending-1",
            "campaign-cycle-draft-1",
            "campaign-cycle-pending-2",
            "campaign-cycle-pending-1",
        ]
    )
    lakebase = _LifecycleLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_portfolio.get_correlation_id",
        lambda: next(request_ids),
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]
    campaign_id = "11111111-1111-4111-8111-111111111111"

    first_pending = repo.patch_status(
        campaign_id,
        CampaignStatusPatchRequest(status="pending_review"),
        actor="manager@example.com",
    )
    lost_response_replay = repo.patch_status(
        campaign_id,
        CampaignStatusPatchRequest(status="pending_review"),
        actor="manager@example.com",
    )
    repo.patch_status(
        campaign_id,
        CampaignStatusPatchRequest(status="draft"),
        actor="manager@example.com",
    )
    second_pending = repo.patch_status(
        campaign_id,
        CampaignStatusPatchRequest(status="pending_review"),
        actor="manager@example.com",
    )
    old_replay_after_later_cycle = repo.patch_status(
        campaign_id,
        CampaignStatusPatchRequest(status="pending_review"),
        actor="manager@example.com",
    )

    assert first_pending == lost_response_replay == old_replay_after_later_cycle
    assert second_pending.status == "pending_review"
    assert second_pending.updated_at != first_pending.updated_at
    assert len(lakebase.audits) == 3
    pending_request_ids = [
        request_id
        for request_id, row in lakebase.audits.items()
        if row["status"] == "pending_review"
    ]
    assert len(pending_request_ids) == 2
    assert pending_request_ids[0] != pending_request_ids[1]


def test_campaign_status_request_identity_payload_mismatch_is_conflict(monkeypatch):
    class _ReplayMismatchLakebase(_CampaignPatchLakebase):
        def fetchone(
            self,
            sql: str,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            if "JOIN mip_app.action_audit" in sql:
                return {
                    **self._row(status="pending_review"),
                    "audit_id": "22222222-2222-4222-8222-222222222222",
                    "audit_metadata": {"status": "pending_review", "rationale": "Original"},
                }
            return self._row(status="pending_review")

    lakebase = _ReplayMismatchLakebase(suppression_policy={"default": "eligible_only"})
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_portfolio.get_correlation_id",
        lambda: "campaign-status-request-mismatch",
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        repo.patch_status(
            "11111111-1111-4111-8111-111111111111",
            CampaignStatusPatchRequest(status="pending_review", rationale="Changed"),
            actor="manager@example.com",
        )

    assert exc_info.value.status_code == 409


def test_campaign_status_lost_update_maps_to_conflict(monkeypatch):
    class _LostUpdateLakebase(_CampaignPatchLakebase):
        def __init__(self) -> None:
            super().__init__(suppression_policy={"default": "eligible_only"})
            self.replay_lookups = 0

        def fetchone(
            self,
            sql: str,
            params: dict[str, Any] | None = None,
        ) -> dict[str, Any] | None:
            self.calls.append({"sql": sql, "params": params or {}})
            if "JOIN mip_app.action_audit" in sql:
                self.replay_lookups += 1
                return None
            if "UPDATE mip_app.campaigns" in sql:
                return None
            return self._row(status="draft")  # type: ignore[return-value]

    lakebase = _LostUpdateLakebase()
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_portfolio.time.sleep",
        lambda _delay: None,
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]

    with pytest.raises(HTTPException) as exc_info:
        repo.patch_status(
            "11111111-1111-4111-8111-111111111111",
            CampaignStatusPatchRequest(status="pending_review"),
            actor="manager@example.com",
        )

    assert exc_info.value.status_code == 409
    assert lakebase.replay_lookups == 4


@pytest.mark.parametrize(
    "suppression_policy",
    [
        {"default": "eligible_only", "frequency_cap_days": 30},
        {"require_marketing_eligible": True, "frequency_cap_days": 30},
        {"marketing_eligibility": "Eligible only", "frequency_cap_days": 30},
        {"marketing_eligibility": "eligible_only", "frequency_cap_days": 30},
    ],
)
def test_campaign_status_accepts_reviewed_eligible_only_policy_shapes(
    monkeypatch,
    suppression_policy: dict[str, object],
):
    lakebase = _CampaignPatchLakebase(suppression_policy=suppression_policy)
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]

    summary = repo.patch_status(
        "11111111-1111-4111-8111-111111111111",
        CampaignStatusPatchRequest(status="pending_review"),
        actor="skyler@entrada.ai",
    )

    assert summary.status == "pending_review"
    patch_calls = [
        call for call in lakebase.calls if "UPDATE mip_app.campaigns" in str(call["sql"])
    ]
    assert len(patch_calls) == 1
    patch_call = patch_calls[0]
    assert "INSERT INTO mip_app.action_audit" in str(patch_call["sql"])
    assert "request_id" in str(patch_call["sql"])
    assert patch_call["params"]["actor"] == "skyler@entrada.ai"
    assert str(patch_call["params"]["request_id"]).startswith("campaign-status-")
    metadata = json.loads(str(patch_call["params"]["metadata"]))
    assert metadata == {
        "action": "campaign.status_update",
        "rationale": None,
        "status": "pending_review",
    }


@pytest.mark.parametrize(
    ("current_status", "target_status"),
    [
        ("pending_review", "approved"),
        ("approved", "live"),
        ("live", "active"),
    ],
)
def test_governed_campaign_statuses_reject_missing_server_authorization(
    monkeypatch,
    current_status: str,
    target_status: str,
):
    lakebase = _CampaignPatchLakebase(
        suppression_policy={"default": "eligible_only"},
        current_status=current_status,
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]

    with pytest.raises(ValueError, match="governed approver authorization"):
        repo.patch_status(
            "11111111-1111-4111-8111-111111111111",
            CampaignStatusPatchRequest(
                status=target_status,  # type: ignore[arg-type]
                rationale="Governance and legal review completed",
            ),
            actor="approver@example.com",
        )


def test_approved_campaign_transition_persists_signed_approval_evidence(monkeypatch):
    campaign_id = "11111111-1111-4111-8111-111111111111"
    actor = "approver@example.com"
    lakebase = _CampaignPatchLakebase(
        suppression_policy={"default": "eligible_only"},
        current_status="pending_review",
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]
    authorized_at = datetime.now(UTC)
    payload = authorize_campaign_status_transition(
        CampaignStatusPatchRequest(
            status="approved",
            rationale="Governance and legal review completed",
        ),
        campaign_id=campaign_id,
        current_status="pending_review",
        approver_email=actor,
        now=authorized_at,
    )

    summary = repo.patch_status(campaign_id, payload, actor=actor)

    assert summary.status == "approved"
    patch_call = next(
        call for call in lakebase.calls if "UPDATE mip_app.campaigns" in str(call["sql"])
    )
    params = patch_call["params"]
    assert isinstance(params, dict)
    assert params["current_status"] == "pending_review"
    assert len(params["evidence_ids"]) == 1
    assert "AND status = %(current_status)s" in str(patch_call["sql"])
    metadata = json.loads(str(params["metadata"]))
    assert metadata["approval_id"] == params["evidence_ids"][0]
    assert metadata["occurred_at"] == authorized_at.isoformat()
    assert metadata["status"] == "approved"


def test_campaign_approval_evidence_is_bound_to_the_authorized_actor(monkeypatch):
    campaign_id = "11111111-1111-4111-8111-111111111111"
    lakebase = _CampaignPatchLakebase(
        suppression_policy={"default": "eligible_only"},
        current_status="pending_review",
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]
    payload = authorize_campaign_status_transition(
        CampaignStatusPatchRequest(
            status="approved",
            rationale="Governance and legal review completed",
        ),
        campaign_id=campaign_id,
        current_status="pending_review",
        approver_email="authorized@example.com",
    )

    with pytest.raises(ValueError, match="approval evidence does not match"):
        repo.patch_status(campaign_id, payload, actor="different@example.com")

    with pytest.raises(ValueError, match="governed approver identity"):
        authorize_campaign_status_transition(
            CampaignStatusPatchRequest(
                status="approved",
                rationale="Governance and legal review completed",
            ),
            campaign_id=campaign_id,
            current_status="pending_review",
            approver_email=" ",
        )


def test_campaign_status_rejects_illegal_skip_even_with_approver_proof(monkeypatch):
    campaign_id = "11111111-1111-4111-8111-111111111111"
    actor = "approver@example.com"
    lakebase = _CampaignPatchLakebase(
        suppression_policy={"default": "eligible_only"},
        current_status="draft",
    )
    monkeypatch.setattr(
        "backend.services.repositories.databricks_repo.get_lakebase_client",
        lambda: lakebase,
    )
    repo = DatabricksPortfolioRepository(_StubClient(_preview_row(), []))  # type: ignore[arg-type]
    payload = authorize_campaign_status_transition(
        CampaignStatusPatchRequest(status="approved", rationale="Governance review completed"),
        campaign_id=campaign_id,
        current_status="draft",
        approver_email=actor,
    )

    with pytest.raises(ValueError, match="from draft to approved is not allowed"):
        repo.patch_status(campaign_id, payload, actor=actor)


def test_empty_filtered_cohort_keeps_avg_score_null_not_zero():
    client = _StubClient(
        {
            "marketable_population": 0,
            "high_intent_leads": 0,
            "top_tier_opportunities": None,
            "offers_recommended": None,
            "avg_score": None,
        },
        [_trend_row("2026-04-22T18:30:00")],
    )
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.avg_score is None
