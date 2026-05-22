"""Live SQL invariants for Module 0 data truth.

These checks reconcile the app-facing gold tables and semantic views against
their canonical source tables. They are intentionally warehouse-gated: local CI
without Databricks credentials skips, while release validation runs them against
the deployed workspace before a demo/shipment claim.
"""
from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from typing import Any

import pytest


def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
    token = os.environ.get("DATABRICKS_TOKEN")
    warehouse_id = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not token or not warehouse_id:
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, warehouse_id


def _run_sql_rows(
    host: str,
    token: str,
    warehouse_id: str,
    statement: str,
) -> list[list[Any]]:
    url = f"{host}/api/2.0/sql/statements/"
    payload = json.dumps(
        {
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "30s",
            "on_wait_timeout": "CANCEL",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        }
    ).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method="POST",
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=35) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.URLError as exc:  # pragma: no cover -- network
        pytest.skip(f"warehouse unreachable: {exc}")
    status = body.get("status", {}).get("state")
    if status != "SUCCEEDED":
        err = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(f"warehouse statement failed: state={status!r} err={err!r}")
    result = body.get("result", {})
    return result.get("data_array") or []


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "SQL integration test SKIPPED: set DATABRICKS_HOST (or "
            "DATABRICKS_SERVER_HOSTNAME), DATABRICKS_TOKEN, and "
            "DATABRICKS_WAREHOUSE_ID to enable."
        )
    return creds


def test_lead_scores_match_borrower_360_app_scores(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT
          COUNT(*) AS compared,
          SUM(CASE WHEN ls.opportunity_score <> b.opportunity_score THEN 1 ELSE 0 END) AS score_mismatches,
          SUM(CASE WHEN ls.confidence <> b.confidence THEN 1 ELSE 0 END) AS confidence_mismatches
        FROM mip.gold.lead_scores AS ls
        JOIN mip.gold.borrower_360 AS b USING (clip)
        """,
    )
    assert rows, "score parity query returned no rows"
    compared, score_mismatches, confidence_mismatches = map(int, rows[0])
    assert compared > 0
    assert score_mismatches == 0
    assert confidence_mismatches == 0


def test_lead_population_score_columns_match_borrower_360(
    warehouse: tuple[str, str, str],
) -> None:
    """Ranked lead rows must not drift from the borrower dossier score surface."""
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT
          COUNT(*) AS compared,
          SUM(CASE WHEN lp.opportunity_score <> b.opportunity_score THEN 1 ELSE 0 END) AS score_mismatches,
          SUM(CASE WHEN lp.rate_spread_bps <> b.rate_spread_bps THEN 1 ELSE 0 END) AS spread_mismatches,
          SUM(CASE WHEN lp.equity_pct <> b.equity_pct THEN 1 ELSE 0 END) AS equity_mismatches
        FROM mip.gold.lead_population AS lp
        JOIN mip.gold.borrower_360 AS b USING (clip)
        """,
    )
    assert rows, "lead population parity query returned no rows"
    compared, score_mismatches, spread_mismatches, equity_mismatches = map(int, rows[0])
    assert compared > 0
    assert score_mismatches == 0
    assert spread_mismatches == 0
    assert equity_mismatches == 0


def test_borrower_opportunity_metric_view_is_borrower_grain(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT
          (SELECT COUNT(*) FROM mip.gold.borrower_360) AS borrower_360_rows,
          (SELECT COUNT(*) FROM mip.semantics.borrower_opportunity_metric_view) AS view_rows,
          (SELECT COUNT(DISTINCT clip) FROM mip.semantics.borrower_opportunity_metric_view) AS view_distinct_clips
        """,
    )
    assert rows, "metric-view grain query returned no rows"
    borrower_360_rows, view_rows, view_distinct_clips = map(int, rows[0])
    assert borrower_360_rows > 0
    assert view_rows == borrower_360_rows
    assert view_distinct_clips == borrower_360_rows


def test_lead_generation_rank_bucket_names_are_literal_truth(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT COUNT(*) AS mislabeled
        FROM mip.semantics.lead_generation_metric_view
        WHERE rank_bucket = 'top_10000'
          AND rank_overall > 10000
        """,
    )
    assert rows, "rank-bucket query returned no rows"
    assert int(rows[0][0]) == 0


def test_avm_as_of_date_parses_when_avm_source_dates_exist(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT
          COUNT(*) AS avm_rows,
          COUNT(avm_as_of_date) AS parsed_avm_dates,
          COUNT_IF(ingest_ts IS NOT NULL) AS avm_rows_with_ingest_ts
        FROM mip.silver.lien_current
        WHERE avm_value IS NOT NULL
        """,
    )
    assert rows, "AVM date query returned no rows"
    avm_rows, parsed_avm_dates, avm_rows_with_ingest_ts = map(int, rows[0])
    if avm_rows == 0:
        pytest.skip("No AVM rows present in this workspace share.")
    if parsed_avm_dates == 0:
        assert avm_rows_with_ingest_ts > 0
        readiness = _run_sql_rows(
            host,
            token,
            wid,
            """
            SELECT status, last_updated, note
            FROM mip.gold.source_readiness
            WHERE source_name = 'AVM'
            """,
        )
        assert readiness, "AVM source-readiness row returned no rows"
        status, last_updated, note = readiness[0]
        assert status == "live"
        assert last_updated is not None
        assert "ingest timestamp" in str(note)
    else:
        assert parsed_avm_dates > 0


def test_synthetic_first_party_feeds_are_not_marked_live(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT COUNT(*) AS bad_rows
        FROM mip.gold.source_readiness
        WHERE synthetic_demo = TRUE
          AND status = 'live'
        """,
    )
    assert rows, "source-readiness demo disclosure query returned no rows"
    assert int(rows[0][0]) == 0


def test_lead_population_matches_borrower_360_score_floor(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT
          (SELECT COUNT(*) FROM mip.gold.lead_population) AS lead_rows,
          (SELECT COUNT(DISTINCT clip) FROM mip.gold.lead_population) AS lead_distinct_clips,
          (SELECT COUNT(*) FROM mip.gold.borrower_360 WHERE opportunity_score >= 50) AS borrower_floor_rows,
          (SELECT COUNT(DISTINCT clip) FROM mip.gold.borrower_360 WHERE opportunity_score >= 50) AS borrower_floor_distinct_clips,
          (SELECT COUNT(*) FROM mip.gold.lead_population WHERE opportunity_score < 50) AS below_floor_rows
        """,
    )
    assert rows, "lead population floor reconciliation returned no rows"
    (
        lead_rows,
        lead_distinct_clips,
        borrower_floor_rows,
        borrower_floor_distinct_clips,
        below_floor_rows,
    ) = map(int, rows[0])
    assert lead_rows > 0
    assert lead_rows == lead_distinct_clips
    assert borrower_floor_rows == borrower_floor_distinct_clips
    assert lead_rows == borrower_floor_rows
    assert below_floor_rows == 0


def test_lead_generation_metric_view_preserves_lead_population_grain(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT
          (SELECT COUNT(*) FROM mip.gold.lead_population) AS lead_rows,
          (SELECT COUNT(DISTINCT clip) FROM mip.gold.lead_population) AS lead_distinct_clips,
          (SELECT COUNT(*) FROM mip.semantics.lead_generation_metric_view) AS view_rows,
          (SELECT COUNT(DISTINCT clip) FROM mip.semantics.lead_generation_metric_view) AS view_distinct_clips
        """,
    )
    assert rows, "lead-generation metric-view grain reconciliation returned no rows"
    lead_rows, lead_distinct_clips, view_rows, view_distinct_clips = map(int, rows[0])
    assert lead_rows > 0
    assert lead_rows == lead_distinct_clips
    assert view_rows == lead_rows
    assert view_distinct_clips == lead_rows


def test_evidence_events_are_scoped_to_borrower_spine(
    warehouse: tuple[str, str, str],
) -> None:
    host, token, wid = warehouse
    rows = _run_sql_rows(
        host,
        token,
        wid,
        """
        SELECT COUNT(*) AS orphan_evidence_rows
        FROM mip.gold.evidence_events AS e
        LEFT ANTI JOIN mip.gold.borrower_360 AS b
          ON b.clip = e.clip
        """,
    )

    assert rows, "evidence orphan query returned no rows"
    assert int(rows[0][0]) == 0
