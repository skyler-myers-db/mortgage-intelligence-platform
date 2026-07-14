from __future__ import annotations

import json
import re
from pathlib import Path

from backend.config.settings import settings
from backend.schemas.lead import Borrower360
from backend.schemas.why import WhyPanel
from backend.services.repositories.databricks_borrowers import DatabricksBorrowerRepository
from backend.services.resilience import TTLCache
from tools.load_test import baseline

ROOT = Path(__file__).resolve().parents[2]


def test_load_harness_has_opt_in_write_path_coverage() -> None:
    source = (ROOT / "tools" / "load_test" / "locustfile.py").read_text(encoding="utf-8")

    assert "MIP_LOAD_TEST_WRITE" in source
    assert "MIP_LOAD_TEST_BORROWER_POOL_SIZE" in source
    for endpoint in (
        "outreach/draft",
        "outreach/approve",
        "portfolio/create",
        "genie/message",
        "genie/actions",
    ):
        assert endpoint in source
    assert "LOAD_TEST_WRITE_ENABLED" in source
    assert "MipUser.tasks.extend" in source


def test_committed_load_baseline_covers_read_and_write_contracts() -> None:
    payload = json.loads(
        (ROOT / "tools" / "load_test" / "baseline.json").read_text(encoding="utf-8")
    )
    endpoints = payload["endpoints"]

    for key in (
        "GET /api/v1/health",
        "POST /api/v1/portfolio/preview",
        "GET /api/v1/leads",
        "GET /api/v1/borrowers/{id}",
        "GET /api/v1/segments",
        "POST /api/v1/outreach/draft",
        "POST /api/v1/outreach/approve",
        "POST /api/v1/portfolio/create",
        "POST /api/v1/genie/message",
        "POST /api/v1/genie/actions",
    ):
        assert key in endpoints
        assert endpoints[key]["p95_budget_ms"] > 0
        assert endpoints[key]["failure_rate_budget_pct"] <= 2.0


def test_baseline_comparison_flags_budget_regression(tmp_path: Path) -> None:
    stats = tmp_path / "stats.csv"
    stats.write_text(
        "\n".join(
            [
                "Type,Name,Request Count,Failure Count,Requests/s,50%,95%,99%",
                "GET,/api/v1/health,10,0,1.0,100,900,1000",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    observed = baseline.read_locust_stats(stats)
    regressions = baseline.compare(
        observed,
        {
            "global_failure_rate_budget_pct": 2.0,
            "regression_tolerance_pct": 25.0,
            "endpoints": {
                "GET /api/v1/health": {
                    "p95_budget_ms": 500,
                    "p95_ms": 130,
                    "failure_rate_budget_pct": 2.0,
                }
            },
        },
    )

    assert any("exceeds budget" in item for item in regressions)
    assert any("exceeds baseline tolerance" in item for item in regressions)


def test_write_baseline_comparison_skips_read_latency_noise(tmp_path: Path) -> None:
    stats = tmp_path / "stats.csv"
    stats.write_text(
        "\n".join(
            [
                "Type,Name,Request Count,Failure Count,Requests/s,50%,95%,99%",
                "GET,/api/v1/health,4,0,0.1,500,1300,1300",
                "POST,/api/v1/outreach/approve,4,0,0.1,1200,1400,1500",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    observed = baseline.read_locust_stats(stats)
    regressions = baseline.compare(
        observed,
        {
            "global_failure_rate_budget_pct": 2.0,
            "regression_tolerance_pct": 25.0,
            "endpoints": {
                "GET /api/v1/health": {
                    "profile": "read",
                    "p95_budget_ms": 500,
                    "p95_ms": 140,
                    "failure_rate_budget_pct": 2.0,
                },
                "POST /api/v1/outreach/approve": {
                    "profile": "write-opt-in",
                    "p95_budget_ms": 2000,
                    "p95_ms": 1400,
                    "failure_rate_budget_pct": 2.0,
                },
            },
        },
        write_enabled=True,
    )

    assert regressions == []


def test_write_baseline_refresh_preserves_read_only_measurements(tmp_path: Path) -> None:
    baseline_path = tmp_path / "baseline.json"
    baseline_path.write_text(
        json.dumps(
            {
                "global_failure_rate_budget_pct": 2.0,
                "regression_tolerance_pct": 25.0,
                "endpoints": {
                    "GET /api/v1/health": {
                        "profile": "read",
                        "p95_budget_ms": 500,
                        "p95_ms": 140,
                        "failure_rate_budget_pct": 2.0,
                    },
                    "POST /api/v1/outreach/approve": {
                        "profile": "write-opt-in",
                        "p95_budget_ms": 2000,
                        "p95_ms": None,
                        "failure_rate_budget_pct": 2.0,
                    },
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    stats = tmp_path / "stats.csv"
    stats.write_text(
        "\n".join(
            [
                "Type,Name,Request Count,Failure Count,Requests/s,50%,95%,99%",
                "GET,/api/v1/health,4,0,0.1,500,1300,1300",
                "POST,/api/v1/outreach/approve,4,0,0.1,1200,1400,1500",
            ]
        )
        + "\n",
        encoding="utf-8",
    )

    baseline.write_baseline(
        path=baseline_path,
        stats=baseline.read_locust_stats(stats),
        target="https://example.test/api/v1",
        write_enabled=True,
    )

    payload = json.loads(baseline_path.read_text(encoding="utf-8"))
    assert payload["endpoints"]["GET /api/v1/health"]["p95_ms"] == 140
    assert payload["endpoints"]["POST /api/v1/outreach/approve"]["p95_ms"] == 1400


def test_lakebase_pool_size_matches_app_concurrency_limit() -> None:
    assert settings.mip_lakebase_pool_max_size == settings.mip_lakebase_concurrency_limit
    assert settings.mip_genie_concurrency_limit >= 6


def test_borrower_dossier_repository_serves_cached_copies_without_warehouse_call() -> None:
    class _Client:
        def execute_one(self, *_args: object, **_kwargs: object) -> None:
            raise AssertionError("cache hit should not call the warehouse")

    borrower = Borrower360(
        borrower_id="B-CACHED",
        display_name="Borrower cached",
        city="Chicago",
        state="IL",
        zip="60617",
        segment_codes=["itm"],
        equity_estimate=100000,
        rate_spread_bps=150,
        opportunity_score=80,
        confidence=80,
        recommended_offer="Refinance + HELOC",
        why_now="test",
        evidence_ids=[],
        approval_status="pending",
        clip_id="clip_demo_cached",
        owner_link_id="ol_demo_cached",
        subject_property="Chicago, IL 60617",
        avm_value=250000,
        current_lien_balance=150000,
        current_rate=0.071,
        ltv=60,
        related_property_count=1,
        trigger_timeline=[],
        evidence_events=[],
        why_panel=WhyPanel(
            rate_spread_bps=150,
            market_rate=0.061,
            equity_pct=40,
            in_the_money=True,
            in_the_money_reason="test",
            min_spread_bps=75,
            min_equity_pct=15,
            sources=[],
        ),
    )
    cache = TTLCache()
    cache.set("borrower_dossier:B-CACHED", borrower, ttl_s=60)
    repo = DatabricksBorrowerRepository(
        _Client(),  # type: ignore[arg-type]
        cache=cache,
        cache_ttl_s=60,
    )

    first = repo.get("B-CACHED")
    assert first is not None
    first.segment_codes.append("listed")
    second = repo.get("B-CACHED")

    assert second is not None
    assert second.segment_codes == ["itm"]


def test_borrower_fresh_read_invalidates_cached_dossier() -> None:
    class _Client:
        pass

    cache = TTLCache()
    cache.set("borrower_dossier:B-CACHED", object(), ttl_s=60)
    repo = DatabricksBorrowerRepository(
        _Client(),  # type: ignore[arg-type]
        cache=cache,
        cache_ttl_s=60,
    )
    repo.get = lambda borrower_id: cache.get(f"borrower_dossier:{borrower_id}")  # type: ignore[method-assign,return-value]

    assert repo.get_fresh("B-CACHED") is None


def test_operator_docs_explain_write_load_and_process_local_cache() -> None:
    runbook = (ROOT / "docs" / "runbook.md").read_text(encoding="utf-8")
    readme = (ROOT / "tools" / "load_test" / "README.md").read_text(encoding="utf-8")

    assert "MIP_LOAD_TEST_WRITE=1" in runbook
    assert "process-local by design" in runbook
    assert re.search(r"six\s+concurrent\s+genie\s+turns", runbook, re.IGNORECASE)
    assert "tools/load_test/baseline.json" in readme
    assert "create real rows in Lakebase" in readme
    assert "warmup" in readme.lower()
