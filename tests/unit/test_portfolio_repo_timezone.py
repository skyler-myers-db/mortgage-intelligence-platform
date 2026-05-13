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

from backend.schemas.portfolio import (
    CampaignStatusPatchRequest,
    PortfolioCreateRequest,
    PortfolioCriteria,
    PortfolioPreviewRequest,
)
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
    ):
        self._preview_row = preview_row
        self._trend_rows = trend_rows
        self._day_zero_row = day_zero_row if day_zero_row is not None else {"day_zero": False}
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
        if self._is_day_zero_sql(sql):
            return [self._day_zero_row]
        return [self._preview_row]

    def execute_one(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.statements.append(sql)
        self.parameters.append(params)
        if "funnel_snapshot_daily" in sql:
            return self._trend_rows[0] if self._trend_rows else {}
        if self._is_day_zero_sql(sql):
            return self._day_zero_row
        self.preview_calls += 1
        return self._preview_row


class _StubLakebase:
    def __init__(self) -> None:
        self.rows: list[dict[str, object]] = []

    def fetchone(self, sql: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        self.rows.append({"sql": sql, "params": params or {}})
        return {
            "campaign_id": "11111111-1111-1111-1111-111111111111",
            "audit_id": "22222222-2222-2222-2222-222222222222",
        }


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
    def __init__(self, *, suppression_policy: dict[str, object]) -> None:
        self.suppression_policy = suppression_policy
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
        if "UPDATE mip_app.campaigns" in sql:
            return self._row(status=str((params or {}).get("status") or "pending_review"))  # type: ignore[return-value]
        return self._row()  # type: ignore[return-value]

    def execute(self, sql: str, params: dict[str, Any] | None = None) -> None:
        self.calls.append({"sql": sql, "params": params or {}})


def _preview_row() -> dict[str, Any]:
    return {
        "marketable_population": 1000,
        "high_intent_leads": 300,
        "top_tier_opportunities": 120,
        "offers_recommended": 250,
        "avg_score": 72,
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


def test_naive_datetime_is_stamped_as_utc():
    """Tz-naive datetimes from the connector must come out as tz-aware UTC."""
    naive = datetime(2026, 4, 22, 18, 30, 0)
    client = _StubClient(_preview_row(), [_trend_row(naive)])
    repo = DatabricksPortfolioRepository(client)  # type: ignore[arg-type]

    preview = repo.preview(None)

    assert preview.data_refreshed_at is not None
    assert preview.data_refreshed_at.tzinfo is not None, (
        "tz-naive datetime leaked through — browser will interpret as local time"
    )
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
    client = _StubClient(_preview_row(), [_trend_row(aware)])
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
    client = _StubClient(_preview_row(), [_trend_row("2026-04-22T18:30:00")])
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
            self, sql: str, params: dict[str, Any] | None = None,
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
    k1 = DatabricksPortfolioRepository._preview_cache_key(
        "WHERE a=:a AND b=:b", {"a": 1, "b": 2}
    )
    k2 = DatabricksPortfolioRepository._preview_cache_key(
        "WHERE a=:a AND b=:b", {"b": 2, "a": 1}
    )
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
    assert calls_after_first == calls_after_second == 1, (
        f"second call should have hit cache; saw {calls_after_second} preview SELECTs"
    )


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
    assert trend.note == "Comparison starts on 2026-04-23 because earlier snapshots predate this metric."


def test_trend_step_change_adds_presenter_caution_note():
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
    assert "Material step change on 2026-05-07" in trend.note


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
        )
    )

    preview_index = next(i for i, sql in enumerate(client.statements) if "borrower_360" in sql)
    preview_sql = client.statements[preview_index]
    preview_params = client.parameters[preview_index]
    assert "is_owner_occupied = TRUE" in preview_sql
    assert "equity_pct >= :equity_floor" in preview_sql
    assert "marketing_eligible = TRUE" in preview_sql
    assert preview_params == {"equity_floor": 25}
    assert lakebase.rows
    assert "mip_app.action_audit" in lakebase.rows[0]["sql"]
    assert lakebase.rows[0]["params"]["criteria"] == (
        '{"occupancy": "Owner-occupied", "marketing_eligibility": "Eligible only", '
        '"min_equity_pct_label": "\\u2265 25%"}'
    )
    metadata = json.loads(str(lakebase.rows[0]["params"]["metadata"]))
    assert metadata["source"] == "portfolio_builder"
    assert metadata["criteria"] == {
        "occupancy": "Owner-occupied",
        "marketing_eligibility": "Eligible only",
        "min_equity_pct_label": "≥ 25%",
    }
    assert metadata["marketable_population"] == 1000


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
        call for call in lakebase.calls
        if "UPDATE mip_app.campaigns" in str(call["sql"])
    ]
    assert len(patch_calls) == 1
    patch_call = patch_calls[0]
    assert "INSERT INTO mip_app.action_audit" in str(patch_call["sql"])
    assert patch_call["params"]["actor"] == "skyler@entrada.ai"
    metadata = json.loads(str(patch_call["params"]["metadata"]))
    assert metadata == {
        "action": "campaign.status_update",
        "rationale": None,
        "status": "pending_review",
    }


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
