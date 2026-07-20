"""Segment-count parity: raw Cotality share vs. `mip.gold.borrower_360`.

This is the highest-priority accuracy test for Slice 13. It answers the
question:

    When the app shows "Investor segment: N borrowers," does that number
    actually match what the raw Cotality Delta Share contains for the
    refreshed source coverage?

For each segment:

    * ``itm``       -- rate_spread_bps >= 75 AND equity_pct >= 15
    * ``investor``  -- related_property_count >= 2 OR owner_is_corporate
                       OR is_absentee
    * ``equity``    -- equity_pct >= 35 AND second_pos_amount IS NULL
    * ``retention`` -- is_current_customer AND (rate_spread_bps >= 50
                       OR is_competitor_lien OR listed_for_sale), where
                       current-customer can come from the lender dictionary
                       or the optional first-party servicing feed.
    * ``listed``   -- current active/under-contract Cotality MLS listing
    * ``permit``   -- legacy segment code displayed as HELOC Intent; backed
                      by Cotality HELOC propensity >= 700. True filed
                      building permits remain separate and pending.

we compute a segment count per discovered source state from TWO independent
paths:

    1. REFERENCE -- an INDEPENDENT query written against
       ``cotality_mortgage_data.corelogic.*`` plus raw ``mip.first_party.*``
       optional lender feeds when a segment explicitly consumes them. The
       reference does not read silver/gold borrower/segment outputs. It reads
       the latest reviewed FRED market-rate row from ``mip.silver
       .market_rates_weekly`` and separately asserts that the row is live and
       fresh; otherwise the test would become stale every weekly FRED publish.
    2. GOLD      -- ``SELECT state, COUNT(*) FROM mip.gold.borrower_360
                     WHERE array_contains(segment_codes, '<segment>')``.

Parity tolerance: a segment count must match within 0.5% per state per
segment, OR be exactly equal when count < 1000 (avoids a 1-row
discrepancy in a small segment looking like 5% drift).

The test is GATED on three env vars: ``DATABRICKS_HOST`` /
``DATABRICKS_TOKEN`` / ``DATABRICKS_WAREHOUSE_ID`` (plus the CLI-OAuth
fallback below). When any is missing, the test SKIPS -- same gating
pattern as ``tests/integration/test_sql_python_parity.py``.

On failure the row printouts at DEBUG level give a triage starting point.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
import time
import urllib.error
import urllib.request
from typing import Any

import pytest

from backend.config.settings import is_placeholder_databricks_config

logger = logging.getLogger(__name__)

# Segment thresholds (must match docs/data-contract-module0.md §5 +
# sql/transformations/gold_borrower_360.sql lines 155-159)
MIN_SPREAD_BPS = 75
MIN_EQUITY_PCT = 15
HELOC_EQUITY_MIN = 35
RETENTION_MIN_SPREAD = 50

SEGMENTS = (
    "itm",
    "investor",
    "equity",
    "retention",
    "listed",
    "permit",
    # S1.3: the only overlay segment derivable from the raw share without
    # reproducing gold-internal joins (amortized UPB, owner explode,
    # lender dictionary). The remaining overlays are pinned by
    # test_overlay_segment_flags_match_membership below, which reconciles
    # the membership array against each overlay's dedicated flag column.
    "second_lien_itm",
)

# Tolerance: we allow 0.5% drift per state per segment. Segment counts
# below 1000 must match exactly (relative-tolerance is misleading at
# small N). The ITM/retention segments on our live data have states as
# small as 9 rows, so we need the absolute branch.
REL_TOLERANCE = 0.005
ABS_TOLERANCE_MIN = 1000


# ---------------------------------------------------------------------------
# Credential resolution -- env vars OR the Databricks CLI OAuth path.
# The CLI path is a tolerated fallback so a developer with only
# `~/.databrickscfg` configured can still run the test locally; CI
# passes the env vars directly.
# ---------------------------------------------------------------------------


def _normalized_workspace_host(host: str) -> str:
    normalized = host.strip().rstrip("/")
    if not normalized.startswith("http"):
        normalized = "https://" + normalized
    return normalized


def _cli_profile_for_host(host: str) -> str | None:
    """Resolve exactly one valid CLI profile bound to the target workspace."""

    try:
        out = subprocess.check_output(
            ["databricks", "auth", "profiles", "-o", "json"],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        profiles = json.loads(out).get("profiles", [])
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return None

    explicit_profile = os.environ.get("DATABRICKS_CONFIG_PROFILE")
    target_host = _normalized_workspace_host(host)
    matches = [
        str(profile.get("name"))
        for profile in profiles
        if profile.get("name")
        and profile.get("valid") is not False
        and _normalized_workspace_host(str(profile.get("host", ""))) == target_host
        and (explicit_profile is None or profile.get("name") == explicit_profile)
    ]
    return matches[0] if len(matches) == 1 else None


def _cli_token() -> tuple[str, str, str] | None:
    """Last-resort credential source: the Databricks CLI's OAuth token.

    Only consulted when env vars are not set. Requires ``databricks``
    on PATH and a configured profile whose host matches
    ``DATABRICKS_HOST`` below. Silently returns None if the CLI call
    fails.
    """
    # Resolve host + warehouse before minting a token so the credential is
    # selected for that exact workspace rather than blindly using DEFAULT.
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    wh = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if not host or not wh:
        # Read from the .env.local at repo root if present. Deliberately
        # minimal parser: no shell expansion, no quoting -- matches the
        # flat KEY=VALUE style already used in that file.
        from pathlib import Path

        env_local = Path(__file__).resolve().parents[2] / ".env.local"
        if env_local.exists():
            for line in env_local.read_text().splitlines():
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                if not host and k == "DATABRICKS_HOST":
                    host = v
                elif not wh and k == "DATABRICKS_WAREHOUSE_ID":
                    wh = v
    if not host or not wh or is_placeholder_databricks_config(host=host, warehouse_id=wh):
        return None
    profile = _cli_profile_for_host(host)
    if profile is None:
        return None
    try:
        out = subprocess.check_output(
            ["databricks", "auth", "token", "-p", profile],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        token = json.loads(out).get("access_token")
    except (
        subprocess.CalledProcessError,
        FileNotFoundError,
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
    ):
        return None
    if not token:
        return None
    return host, token, wh


def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get("DATABRICKS_SERVER_HOSTNAME")
    token = os.environ.get("DATABRICKS_TOKEN")
    wh = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if host and token and wh:
        if is_placeholder_databricks_config(host=host, warehouse_id=wh, token=token):
            return None
        if not host.startswith("http"):
            host = "https://" + host
        return host.rstrip("/"), token, wh
    # Fallback to CLI OAuth.
    fallback = _cli_token()
    if fallback is None:
        return None
    host, token, wh = fallback
    if is_placeholder_databricks_config(host=host, warehouse_id=wh, token=token):
        return None
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, wh


def test_placeholder_databricks_env_values_skip_live_parity(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://<workspace-host>.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_TOKEN", "<pat-or-leave-unset-for-oauth>")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "<sql-warehouse-id>")

    assert _creds() is None


def test_cli_oauth_fallback_selects_profile_for_exact_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://target.cloud.databricks.com/")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123warehouse")
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)
    monkeypatch.delenv("DATABRICKS_CONFIG_PROFILE", raising=False)
    commands: list[list[str]] = []

    def _check_output(command: list[str], **_kwargs: object) -> str:
        commands.append(command)
        if command[1:3] == ["auth", "profiles"]:
            return json.dumps(
                {
                    "profiles": [
                        {
                            "name": "DEFAULT",
                            "host": "https://other.cloud.databricks.com",
                            "valid": True,
                        },
                        {
                            "name": "target-workspace",
                            "host": "https://target.cloud.databricks.com",
                            "valid": True,
                        },
                    ]
                }
            )
        assert command == ["databricks", "auth", "token", "-p", "target-workspace"]
        return json.dumps({"access_token": "workspace-token"})

    monkeypatch.setattr(subprocess, "check_output", _check_output)

    assert _creds() == (
        "https://target.cloud.databricks.com",
        "workspace-token",
        "abc123warehouse",
    )
    assert len(commands) == 2


def test_cli_oauth_fallback_refuses_profile_from_another_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("DATABRICKS_HOST", "https://target.cloud.databricks.com")
    monkeypatch.setenv("DATABRICKS_WAREHOUSE_ID", "abc123warehouse")
    monkeypatch.setenv("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
    monkeypatch.delenv("DATABRICKS_TOKEN", raising=False)

    monkeypatch.setattr(
        subprocess,
        "check_output",
        lambda *_args, **_kwargs: json.dumps(
            {
                "profiles": [
                    {
                        "name": "DEFAULT",
                        "host": "https://other.cloud.databricks.com",
                        "valid": True,
                    }
                ]
            }
        ),
    )

    assert _creds() is None


# ---------------------------------------------------------------------------
# Async Statement Execution API client. Segment counts on a 5.16M-row
# share table on a cold warehouse can exceed the 50s sync wait_timeout
# max; we submit with ``on_wait_timeout=CONTINUE`` and poll.
# ---------------------------------------------------------------------------


def _http_json(url: str, token: str, *, data: Any = None, method: str = "GET") -> dict[str, Any]:
    payload = None if data is None else json.dumps(data).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=payload,
        method=method,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
    )
    with urllib.request.urlopen(req, timeout=60) as resp:  # noqa: S310
        return json.loads(resp.read().decode("utf-8"))


def _run_sql(
    host: str, token: str, warehouse_id: str, statement: str, *, budget_s: int = 400
) -> list[list[Any]]:
    """Submit a statement and poll until SUCCEEDED. Returns the data_array.

    Raises pytest.fail on any non-SUCCEEDED terminal state -- this is
    a validation test, not an availability test, and a warehouse error
    means we cannot make a correctness claim.
    """
    body = _http_json(
        f"{host}/api/2.0/sql/statements/",
        token,
        method="POST",
        data={
            "statement": statement,
            "warehouse_id": warehouse_id,
            "wait_timeout": "50s",
            "on_wait_timeout": "CONTINUE",
            "disposition": "INLINE",
            "format": "JSON_ARRAY",
        },
    )
    state = body.get("status", {}).get("state")
    sid = body.get("statement_id")
    deadline = time.time() + budget_s
    while state in ("PENDING", "RUNNING"):
        if time.time() > deadline:
            pytest.fail(f"statement timed out after {budget_s}s: {sid}")
        time.sleep(2.0)
        try:
            body = _http_json(f"{host}/api/2.0/sql/statements/{sid}", token)
        except urllib.error.HTTPError as exc:
            pytest.fail(f"poll failed for {sid}: {exc}")
        state = body.get("status", {}).get("state")
    if state != "SUCCEEDED":
        err = body.get("status", {}).get("error", {}).get("message", "unknown")
        pytest.fail(f"statement {sid} did not succeed (state={state}): {err}")
    return body.get("result", {}).get("data_array") or []


# ---------------------------------------------------------------------------
# Reference queries -- INDEPENDENT. Do NOT reference mip.silver.* or
# mip.gold.*. Every predicate is derived from the raw share so a
# regression in silver/gold cannot silently pass this test.
# ---------------------------------------------------------------------------


def _borrower_economics_reference_ctes() -> str:
    """Raw-share current-balance and equity calculation used by parity paths.

    This deliberately repeats the published 360-month amortization contract
    instead of calling the gold UDF or reading a silver/gold borrower output.
    The only non-source dependency is ``ref.refresh_run_state``: gold captures
    that same immutable timestamp before each rebuild so elapsed-month math
    cannot drift merely because the parity test runs after midnight.
    """
    return """
    refresh_anchor AS (
      SELECT refresh_at
      FROM mip.ref.refresh_run_state
      ORDER BY captured_at DESC
      LIMIT 1
    ),
    src AS (
      SELECT
        situs_state AS state,
        clip,
        CASE
          WHEN first_position_mortgage_interest_rate IS NULL
            OR isnan(CAST(first_position_mortgage_interest_rate AS DOUBLE)) THEN NULL
          WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) < 1 THEN NULL
          WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) > 15 THEN 0.15
          ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
        END AS first_rate_frac,
        CASE
          WHEN second_position_mortgage_interest_rate IS NULL
            OR isnan(CAST(second_position_mortgage_interest_rate AS DOUBLE)) THEN NULL
          WHEN CAST(second_position_mortgage_interest_rate AS DOUBLE) < 1 THEN NULL
          WHEN CAST(second_position_mortgage_interest_rate AS DOUBLE) > 15 THEN 0.15
          ELSE CAST(second_position_mortgage_interest_rate AS DOUBLE) / 100.0
        END AS second_rate_frac,
        TRY_TO_DATE(CAST(NULLIF(first_position_mortgage_date, 0) AS STRING), 'yyyyMMdd')
          AS first_pos_date,
        CAST(first_position_mortgage_amount AS BIGINT) AS first_pos_amount,
        CAST(second_position_mortgage_amount AS BIGINT) AS second_pos,
        CAST(estimated_value_mktg AS BIGINT) AS avm,
        CAST(total_amount_of_open_mortgage_liens AS BIGINT) AS raw_lien,
        CAST(estimated_combined_ltv_loan_to_value AS DOUBLE) AS cltv
      FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
      WHERE situs_state IS NOT NULL
        AND clip IS NOT NULL
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY clip
        ORDER BY
          TRY_TO_DATE(NULLIF(CAST(value_as_of_date_mktg AS STRING), '0'), 'yyyyMMdd') DESC,
          TRY_TO_DATE(CAST(NULLIF(first_position_mortgage_date, 0) AS STRING), 'yyyyMMdd') DESC,
          CAST(total_amount_of_open_mortgage_liens AS BIGINT) DESC
      ) = 1
    ),
    amortization_inputs AS (
      SELECT
        s.*,
        LEAST(360, GREATEST(0, COALESCE(
          CAST(FLOOR(months_between(DATE(ra.refresh_at), s.first_pos_date)) AS INT),
          0
        ))) AS elapsed_months
      FROM src AS s
      CROSS JOIN refresh_anchor AS ra
    ),
    current_balances AS (
      SELECT
        a.*,
        CAST(CASE
          WHEN a.first_pos_amount IS NOT NULL AND a.first_pos_amount > 0 THEN
            CAST(GREATEST(0.0, BROUND(CASE
              WHEN a.elapsed_months >= 360 THEN 0.0
              WHEN a.first_rate_frac IS NULL OR a.first_rate_frac <= 0 THEN
                CAST(a.first_pos_amount AS DOUBLE) * (360 - a.elapsed_months) / 360.0
              ELSE
                CAST(a.first_pos_amount AS DOUBLE)
                * (
                    POWER(1.0 + a.first_rate_frac / 12.0, 360)
                    - POWER(1.0 + a.first_rate_frac / 12.0, a.elapsed_months)
                  )
                / (POWER(1.0 + a.first_rate_frac / 12.0, 360) - 1.0)
            END)) AS BIGINT) + COALESCE(a.second_pos, 0)
          ELSE COALESCE(a.raw_lien, 0)
        END AS BIGINT) AS estimated_current_lien_balance
      FROM amortization_inputs AS a
    ),
    economics AS (
      SELECT
        b.*,
        CAST(CASE
          WHEN b.avm IS NOT NULL AND b.avm > 0 THEN GREATEST(
            0,
            LEAST(
              100,
              100 - GREATEST(
                0,
                ROUND(100.0 * COALESCE(b.estimated_current_lien_balance, 0) / b.avm)
              )
            )
          )
          WHEN b.cltv IS NOT NULL AND b.cltv > 0
            THEN GREATEST(0, LEAST(100, 100 - GREATEST(0, ROUND(b.cltv))))
          ELSE 0
        END AS INT) AS equity_pct
      FROM current_balances AS b
    )
    """


def _itm_reference_sql(mortgage30us_fraction: float) -> str:
    """Reference ITM count per state.

    Mirrors gold_borrower_360.sql WHERE:
        * rate_spread_bps from fn_rate_spread(first_pos_rate, market)
          which is BROUND((current - market) * 10000) per
          sql/uc_functions/fn_rate_spread.sql.
        * equity_pct from AVM and independently amortized current lien balance,
          with Cotality estimated CLTV used only when AVM is unavailable.
        * Rate conversion: share rate is PERCENT (6.40 == 6.40%); divide
          by 100 before feeding to fn_rate_spread. Rates below 1% are
          treated as missing and source outliers above 15% APR clamp to 15%
          before scoring.
    """
    return f"""
    WITH {_borrower_economics_reference_ctes()},
    calc AS (
      SELECT
        state, clip,
        CAST(BROUND((first_rate_frac - {mortgage30us_fraction}) * 10000.0) AS INT)
          AS rate_spread_bps,
        equity_pct
      FROM economics
    )
    SELECT state, COUNT(*) AS n
    FROM calc
    WHERE rate_spread_bps >= {MIN_SPREAD_BPS} AND equity_pct >= {MIN_EQUITY_PCT}
    GROUP BY state ORDER BY state
    """


def _equity_reference_sql() -> str:
    """Reference equity-segment count per state.

    Segment rule (gold_borrower_360.sql line 185):
        equity_pct >= 35 AND COALESCE(second_pos_amount, 0) = 0
    """
    return f"""
    WITH {_borrower_economics_reference_ctes()}
    SELECT state, COUNT(*) AS n
    FROM economics
    WHERE equity_pct >= {HELOC_EQUITY_MIN} AND COALESCE(second_pos, 0) = 0
    GROUP BY state ORDER BY state
    """


def test_equity_references_repeat_current_balance_contract_without_gold_outputs() -> None:
    """The independent path must not regress to original-balance/CLTV-first math."""
    for sql in (
        _itm_reference_sql(0.05),
        _equity_reference_sql(),
        _second_lien_itm_reference_sql(0.05),
    ):
        assert "POWER(1.0 + a.first_rate_frac / 12.0, 360)" in sql
        assert "estimated_current_lien_balance" in sql
        assert "mip.gold.borrower_360" not in sql
        assert "mip.gold.fn_estimated_upb" not in sql


def _investor_reference_sql() -> str:
    """Reference investor-segment count per state.

    Segment rule: related_property_count >= 2
                  OR owner_is_corporate
                  OR is_absentee

    Independent derivation notes:
      * related_property_count is rolled up from owner_1_identifier in
        entrada_eval_property_domain_v3 directly (no mip.gold.property_
        owner_bridge dependency).
      * owner_is_corporate = UPPER(TRIM(owner_1_corporate_indicator))='Y'.
        Probed 2026-04-21: the raw column is STRING with values {'Y',
        NULL} only -- no 'N'. Avoid silver's `CAST(COALESCE(.., 0) AS
        BOOLEAN)` which relies on Spark column-expression coercion.
      * is_absentee = mailing_state IS NOT NULL AND UPPER(TRIM(mailing
        _state)) != UPPER(TRIM(situs_state)).
      * Spine: entrada_eval_voluntary_lien_status_marketing_v2 clip
        (matches gold_borrower_360's LEFT JOIN on clip). A property
        without a lien row is not a borrower and does not land in
        borrower_360.
    """
    return """
    WITH prop6 AS (
      SELECT
        clip,
        situs_state AS state,
        owner_1_identifier AS owner_link,
        (UPPER(TRIM(COALESCE(owner_1_corporate_indicator, ''))) = 'Y') AS is_corp,
        (mailing_state IS NOT NULL
         AND UPPER(TRIM(mailing_state)) <> UPPER(TRIM(situs_state))) AS is_absentee
      FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
      WHERE situs_state IS NOT NULL AND clip IS NOT NULL
    ),
    bridge AS (
      SELECT owner_1_identifier AS owner_link, COUNT(*) AS related_n
      FROM cotality_mortgage_data.corelogic.entrada_eval_property_domain_v3
      WHERE clip IS NOT NULL AND owner_1_identifier IS NOT NULL
      GROUP BY owner_1_identifier
    )
    SELECT p.state, COUNT(*) AS n
    FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2 l
    JOIN prop6 p ON p.clip = l.clip
    LEFT JOIN bridge b ON b.owner_link = p.owner_link
    WHERE l.situs_state IS NOT NULL AND l.clip IS NOT NULL
      AND (COALESCE(b.related_n, 1) >= 2 OR p.is_corp OR p.is_absentee)
    GROUP BY p.state ORDER BY p.state
    """


def _retention_reference_sql(mortgage30us_fraction: float) -> str:
    """Reference retention-segment count per state.

    Segment rule:
        is_current_customer AND
          (rate_spread_bps >= 50 OR is_competitor_lien OR listed_for_sale)

    Independent derivation notes:
      * is_current_customer: current servicer resolves to a non-competitor row
        in mip.ref.lender_dictionary OR raw first_party.servicing_portfolio has
        an active row for the CLIP-derived borrower_id. This keeps the test
        tenant-driven while validating the lender-owned feed contract.
      * is_competitor_lien: servicer known AND not current-customer, matching
        gold_borrower_360. This makes is_current_customer and
        is_competitor_lien mutually exclusive by construction.
      * listed_for_sale: current active/under-contract MLS row, independently
        derived from the raw listing table.
    """
    return f"""
    WITH lien AS (
      SELECT
        clip,
        situs_state,
        first_position_currently_assigned_lender_company_name,
        CASE
          WHEN first_position_mortgage_interest_rate IS NULL THEN NULL
          WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) < 1 THEN NULL
          WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) > 15 THEN 0.15
          ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
        END AS first_pos_rate,
        TRY_TO_DATE(NULLIF(CAST(value_as_of_date_mktg AS STRING), '0'), 'yyyyMMdd')
          AS avm_as_of_date,
        TRY_TO_DATE(CAST(NULLIF(first_position_mortgage_date, 0) AS STRING), 'yyyyMMdd')
          AS first_pos_date,
        CAST(total_amount_of_open_mortgage_liens AS BIGINT) AS total_open_lien_balance
      FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
      WHERE situs_state IS NOT NULL AND clip IS NOT NULL
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY clip
        ORDER BY avm_as_of_date DESC, first_pos_date DESC, total_open_lien_balance DESC
      ) = 1
    ),
    servicing AS (
      SELECT
        borrower_id,
        COUNT_IF(servicing_status = 'active') > 0 AS has_active_servicing
      FROM mip.first_party.servicing_portfolio
      GROUP BY borrower_id
    ),
    listing AS (
      SELECT
        clip,
        (
          UPPER(TRIM(COALESCE(most_recent_listing_indicator_derived, 'N'))) = 'Y'
          AND listing_status_category_code_standardized IN ('A', 'U')
        ) AS is_active_listing
      FROM cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1
      WHERE clip IS NOT NULL
        AND composite_listing_id_derived IS NOT NULL
        AND UPPER(TRIM(COALESCE(situs_state, listing_address_state))) RLIKE '^[A-Z]{{2}}$'
        AND UPPER(TRIM(COALESCE(most_recent_listing_indicator_derived, 'N'))) = 'Y'
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY clip
        ORDER BY
          CASE WHEN listing_status_category_code_standardized IN ('A', 'U') THEN 1 ELSE 0 END DESC,
          TRY_TO_TIMESTAMP(CAST(COALESCE(
            status_change_date_and_time_standardized,
            last_status_change_date,
            last_listing_date_and_time_standardized,
            listing_date,
            original_listing_date
          ) AS STRING)) DESC,
          COALESCE(updated_at, _meta_loaded_at) DESC
      ) = 1
    ),
    calc AS (
      SELECT
        l.situs_state AS state,
        CONCAT('B-', LPAD(UPPER(CONV(CAST(ABS(XXHASH64(l.clip)) AS STRING), 10, 36)), 13, '0')) AS borrower_id,
        (
          COALESCE(NOT lr.is_competitor, FALSE)
          OR COALESCE(s.has_active_servicing, FALSE)
        ) AS is_current_customer,
        CAST(ROUND((
          l.first_pos_rate - {mortgage30us_fraction}) * 10000.0
        ) AS INT) AS spread_bps,
        COALESCE(mls.is_active_listing, FALSE) AS listed_for_sale
      FROM lien l
      LEFT JOIN mip.ref.lender_dictionary lr
        ON UPPER(TRIM(lr.raw_key)) = UPPER(TRIM(l.first_position_currently_assigned_lender_company_name))
      LEFT JOIN servicing s
        ON s.borrower_id = CONCAT('B-', LPAD(UPPER(CONV(CAST(ABS(XXHASH64(l.clip)) AS STRING), 10, 36)), 13, '0'))
      LEFT JOIN listing mls
        ON mls.clip = l.clip
      WHERE l.situs_state IS NOT NULL AND l.clip IS NOT NULL
    )
    SELECT state, COUNT(*) AS n
    FROM calc
    WHERE is_current_customer
      AND (spread_bps >= {RETENTION_MIN_SPREAD} OR listed_for_sale)
    GROUP BY state ORDER BY state
    """


def _listed_reference_sql() -> str:
    """Reference listed-for-sale count per state from raw Cotality MLS rows."""
    return """
    WITH spine AS (
      SELECT clip, situs_state AS state
      FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
      WHERE situs_state IS NOT NULL AND clip IS NOT NULL
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY clip
        ORDER BY
          TRY_TO_DATE(NULLIF(CAST(value_as_of_date_mktg AS STRING), '0'), 'yyyyMMdd') DESC,
          TRY_TO_DATE(CAST(NULLIF(first_position_mortgage_date, 0) AS STRING), 'yyyyMMdd') DESC,
          CAST(total_amount_of_open_mortgage_liens AS BIGINT) DESC
      ) = 1
    ),
    listing AS (
      SELECT
        clip,
        (
          UPPER(TRIM(COALESCE(most_recent_listing_indicator_derived, 'N'))) = 'Y'
          AND listing_status_category_code_standardized IN ('A', 'U')
        ) AS is_active_listing
      FROM cotality_mortgage_data.corelogic.entrada_eval_mls_listing_v1
      WHERE clip IS NOT NULL
        AND composite_listing_id_derived IS NOT NULL
        AND UPPER(TRIM(COALESCE(situs_state, listing_address_state))) RLIKE '^[A-Z]{2}$'
        AND UPPER(TRIM(COALESCE(most_recent_listing_indicator_derived, 'N'))) = 'Y'
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY clip
        ORDER BY
          CASE WHEN listing_status_category_code_standardized IN ('A', 'U') THEN 1 ELSE 0 END DESC,
          TRY_TO_TIMESTAMP(CAST(COALESCE(
            status_change_date_and_time_standardized,
            last_status_change_date,
            last_listing_date_and_time_standardized,
            listing_date,
            original_listing_date
          ) AS STRING)) DESC,
          COALESCE(updated_at, _meta_loaded_at) DESC
      ) = 1
    )
    SELECT s.state, COUNT(*) AS n
    FROM spine s
    JOIN listing l ON l.clip = s.clip
    WHERE l.is_active_listing
    GROUP BY s.state ORDER BY s.state
    """


def _heloc_intent_reference_sql() -> str:
    """Reference HELOC-intent count per state from raw HELOC propensity rows."""
    return """
    WITH spine AS (
      SELECT clip, situs_state AS state
      FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
      WHERE situs_state IS NOT NULL AND clip IS NOT NULL
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY clip
        ORDER BY
          TRY_TO_DATE(NULLIF(CAST(value_as_of_date_mktg AS STRING), '0'), 'yyyyMMdd') DESC,
          TRY_TO_DATE(CAST(NULLIF(first_position_mortgage_date, 0) AS STRING), 'yyyyMMdd') DESC,
          CAST(total_amount_of_open_mortgage_liens AS BIGINT) DESC
      ) = 1
    ),
    heloc AS (
      SELECT
        clip,
        CAST(heloc_model_propensity_score AS INT) AS score
      FROM cotality_mortgage_data.corelogic.entrada_eval_heloc_propensity_score_v1
      WHERE clip IS NOT NULL
        AND UPPER(TRIM(situs_state)) RLIKE '^[A-Z]{2}$'
      QUALIFY ROW_NUMBER() OVER (
        PARTITION BY clip
        ORDER BY heloc_model_propensity_score_run_date DESC NULLS LAST,
                 _meta_loaded_at DESC NULLS LAST
      ) = 1
    )
    SELECT s.state, COUNT(*) AS n
    FROM spine s
    JOIN heloc h ON h.clip = s.clip
    WHERE h.score >= 700
    GROUP BY s.state ORDER BY s.state
    """


def _second_lien_itm_reference_sql(mortgage30us_fraction: float) -> str:
    """Reference second_lien_itm count per state (S1.3).

    Mirrors gold_borrower_360.sql: an OPEN second position
    (second_position_mortgage_amount > 0) whose bounded fractional rate
    clears the SAME governed spread threshold against the first-lien par
    reference, with the shared equity screen. The rate bounding matches
    silver_lien_current.sql (rates below 1% treated as missing; outliers
    above 15% APR clamp to 15%). Equity independently repeats the same
    amortized-current-UPB contract as the first-lien ITM reference.
    """
    return f"""
    WITH {_borrower_economics_reference_ctes()},
    calc AS (
      SELECT
        state, clip, second_pos,
        CAST(BROUND((second_rate_frac - {mortgage30us_fraction}) * 10000.0) AS INT)
          AS rate_spread_bps,
        equity_pct
      FROM economics
    )
    SELECT state, COUNT(*) AS n
    FROM calc
    WHERE COALESCE(second_pos, 0) > 0
      AND rate_spread_bps >= {MIN_SPREAD_BPS}
      AND equity_pct >= {MIN_EQUITY_PCT}
    GROUP BY state ORDER BY state
    """


def _gold_segment_sql(segment_code: str) -> str:
    """Gold-side segment count per state.

    Read from ``mip.gold.borrower_360`` directly (not segment_population)
    so the parity claim is on the row-level membership array, not on
    the downstream aggregate. segment_population is verified separately
    in `test_segment_population_matches_borrower_360`.
    """
    return f"""
    SELECT state, COUNT(*) AS n
    FROM mip.gold.borrower_360
    WHERE array_contains(segment_codes, '{segment_code}')
    GROUP BY state ORDER BY state
    """


# ---------------------------------------------------------------------------
# Query-once, reuse -- module-scoped fixture so the six reference queries
# each run ONCE across the whole test module (not once per state).
# ---------------------------------------------------------------------------


def _rows_to_state_map(rows: list[list[Any]]) -> dict[str, int]:
    return {str(r[0]): int(r[1]) for r in rows}


@pytest.fixture(scope="module")
def warehouse() -> tuple[str, str, str]:
    creds = _creds()
    if creds is None:
        pytest.skip(
            "segment-count parity SKIPPED: set DATABRICKS_HOST + "
            "DATABRICKS_TOKEN + DATABRICKS_WAREHOUSE_ID, or configure "
            "the Databricks CLI DEFAULT profile, to enable."
        )
    return creds


def _latest_mortgage30us_fraction(
    host: str,
    token: str,
    warehouse_id: str,
) -> float:
    """Return the live FRED MORTGAGE30US fraction used by gold.

    This keeps the segment-parity reference in lockstep with the app's
    current market-rate assumption while still failing loudly if the FRED
    ingest has fallen back to seed data or gone stale.
    """
    rows = _run_sql(
        host,
        token,
        warehouse_id,
        """
        SELECT
          CAST(rate_fraction AS DOUBLE) AS rate_fraction,
          source,
          CAST(DATEDIFF(CURRENT_DATE(), observation_week) AS INT) AS age_days
        FROM mip.silver.market_rates_weekly
        WHERE series_id='MORTGAGE30US' AND is_latest=TRUE
        LIMIT 1
        """,
    )
    if not rows:
        pytest.fail("no latest MORTGAGE30US row found in mip.silver.market_rates_weekly")
    rate_fraction, source, age_days = rows[0]
    if str(source) != "fred":
        pytest.fail(
            "latest MORTGAGE30US row is not live FRED data "
            f"(source={source!r}); run mip_fred_rates_ingest before parity validation"
        )
    if int(age_days) > 21:
        pytest.fail(
            "latest MORTGAGE30US row is stale "
            f"({age_days} days old); refresh FRED before parity validation"
        )
    return float(rate_fraction)


def test_borrower_360_market_rate_matches_latest_fred(
    warehouse: tuple[str, str, str],
) -> None:
    """Gold borrower scoring must be rebuilt after each FRED market-rate refresh.

    A stale ``borrower_360.market_rate_fraction`` can leave the row count and
    schema perfectly valid while pushing threshold-adjacent ITM borrowers across
    the 75 bps boundary. This guard fails with an operationally useful message
    before the broader segment-count parity assertion reports state-level drift.
    """
    host, token, wh = warehouse
    latest = _latest_mortgage30us_fraction(host, token, wh)
    rows = _run_sql(
        host,
        token,
        wh,
        """
        SELECT
          MIN(CAST(market_rate_fraction AS DOUBLE)) AS min_gold_market,
          MAX(CAST(market_rate_fraction AS DOUBLE)) AS max_gold_market,
          MIN(refreshed_at) AS min_refreshed_at,
          MAX(refreshed_at) AS max_refreshed_at,
          COUNT(*) AS row_count
        FROM mip.gold.borrower_360
        """,
    )
    assert rows, "borrower_360 market-rate freshness probe returned no rows"
    min_gold, max_gold, min_refreshed, max_refreshed, row_count = rows[0]
    assert int(row_count) > 0, "mip.gold.borrower_360 is empty"
    assert float(min_gold) == pytest.approx(latest, abs=1e-12)
    assert float(max_gold) == pytest.approx(latest, abs=1e-12), (
        "mip.gold.borrower_360 was not rebuilt after the latest FRED "
        "MORTGAGE30US refresh: "
        f"latest={latest}, gold_min={min_gold}, gold_max={max_gold}, "
        f"gold_refreshed_at={min_refreshed}..{max_refreshed}. "
        "Run `databricks bundle run mip_refresh_scores -t dev --profile DEFAULT` "
        "after `mip_fred_rates_ingest`."
    )


def _borrower_360_has_s13_columns(host: str, token: str, warehouse_id: str) -> bool:
    """True when the deployed gold.borrower_360 carries the S1.3 overlay
    columns. A pre-S1.3 table (deploy ordering: this branch's refresh job
    has not run yet) makes the overlay checks meaningless, so they skip
    with an operational message instead of reporting false drift."""
    rows = _run_sql(host, token, warehouse_id, "DESCRIBE TABLE mip.gold.borrower_360")
    columns = {str(r[0]) for r in rows}
    return "second_lien_itm" in columns and "refi_propensity_heuristic" in columns


@pytest.fixture(scope="module")
def counts(warehouse: tuple[str, str, str]) -> dict[str, dict[str, dict[str, int]]]:
    """Returns ``counts[segment][path][state] -> int``.

    path is 'ref' or 'gold'. States are discovered from the refreshed source
    coverage. The legacy ``permit`` segment code is expected to represent
    HELOC Intent until true filed-permit rows are available.
    """
    host, token, wh = warehouse
    mortgage30us_fraction = _latest_mortgage30us_fraction(host, token, wh)

    reference_sqls: dict[str, str] = {
        "itm": _itm_reference_sql(mortgage30us_fraction),
        "equity": _equity_reference_sql(),
        "investor": _investor_reference_sql(),
        "retention": _retention_reference_sql(mortgage30us_fraction),
        "listed": _listed_reference_sql(),
        "permit": _heloc_intent_reference_sql(),
        "second_lien_itm": _second_lien_itm_reference_sql(mortgage30us_fraction),
    }

    segments = SEGMENTS
    if not _borrower_360_has_s13_columns(host, token, wh):
        logger.warning(
            "gold.borrower_360 pre-dates S1.3 overlay columns; skipping "
            "second_lien_itm parity until mip_refresh_scores runs on this branch"
        )
        segments = tuple(s for s in SEGMENTS if s != "second_lien_itm")

    out: dict[str, dict[str, dict[str, int]]] = {}
    for seg in segments:
        ref: dict[str, int] = {}
        if seg in reference_sqls:
            t0 = time.time()
            rows = _run_sql(host, token, wh, reference_sqls[seg])
            logger.debug("REF %s (%.1fs): %s", seg, time.time() - t0, rows)
            ref.update(_rows_to_state_map(rows))
        gold_rows = _run_sql(host, token, wh, _gold_segment_sql(seg))
        logger.debug("GOLD %s: %s", seg, gold_rows)
        gold = _rows_to_state_map(gold_rows)
        out[seg] = {"ref": ref, "gold": gold}
    return out


# ---------------------------------------------------------------------------
# Segment/state parity test.
# ---------------------------------------------------------------------------


def test_segment_count_parity(
    counts: dict[str, dict[str, dict[str, int]]],
) -> None:
    """One assertion path per discovered (segment, state)."""
    assert any(paths["ref"] or paths["gold"] for paths in counts.values())
    for segment in counts:
        ref_map = counts[segment]["ref"]
        gold_map = counts[segment]["gold"]

        for state in sorted(set(ref_map) | set(gold_map)):
            ref_n = ref_map.get(state, 0)
            gold_n = gold_map.get(state, 0)
            delta = abs(ref_n - gold_n)
            if ref_n < ABS_TOLERANCE_MIN or gold_n < ABS_TOLERANCE_MIN:
                assert ref_n == gold_n, (
                    f"{segment}/{state}: ref={ref_n} gold={gold_n} "
                    f"delta={delta} (exact match required below {ABS_TOLERANCE_MIN})"
                )
                continue
            pct = delta / max(ref_n, 1)
            assert pct <= REL_TOLERANCE, (
                f"{segment}/{state}: ref={ref_n} gold={gold_n} "
                f"delta={delta} ({pct * 100:.2f}% > {REL_TOLERANCE * 100}%)"
            )


# ---------------------------------------------------------------------------
# Bonus parity checks: total row count + segment_population consistency.
# ---------------------------------------------------------------------------


def test_borrower_360_total_rows(warehouse: tuple[str, str, str]) -> None:
    """``gold.borrower_360`` row count must equal the source share row
    count for all non-null states (exact -- no aggregation, just a lift/join). A drift here
    means the gold CTAS dropped or double-counted rows during its
    lien_current join, which would invalidate every segment count."""
    host, token, wh = warehouse
    rows = _run_sql(
        host,
        token,
        wh,
        """
        SELECT
          (SELECT COUNT(*) FROM mip.gold.borrower_360) AS b360,
          (SELECT COUNT(*)
           FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
           WHERE situs_state IS NOT NULL
             AND clip IS NOT NULL) AS source_count
        """,
    )
    assert rows, "no rows returned for total-count probe"
    b360, source_count = int(rows[0][0]), int(rows[0][1])
    assert b360 == source_count, (
        f"gold.borrower_360={b360:,} but share(non-null states)={source_count:,} "
        f"-- drift of {abs(b360 - source_count)} rows. Check gold_borrower_"
        f"360.sql join predicates."
    )


def test_segment_population_matches_borrower_360(
    warehouse: tuple[str, str, str],
) -> None:
    """``gold.segment_population`` is a pure aggregate of borrower_360.
    This test reconciles the per-(segment, state) count between the
    two so a stale segment_population can't silently drive the UI to
    wrong numbers."""
    host, token, wh = warehouse
    sp_rows = _run_sql(
        host,
        token,
        wh,
        """
        SELECT segment_code, state, count
        FROM mip.gold.segment_population
        WHERE state <> '_ALL'
        ORDER BY segment_code, state
        """,
    )
    sp = {(r[0], r[1]): int(r[2]) for r in sp_rows}

    b360_rows = _run_sql(
        host,
        token,
        wh,
        """
        SELECT sc AS segment_code, state, COUNT(*) AS n
        FROM mip.gold.borrower_360
        LATERAL VIEW EXPLODE(segment_codes) t AS sc
        GROUP BY sc, state
        ORDER BY sc, state
        """,
    )
    b360 = {(r[0], r[1]): int(r[2]) for r in b360_rows}

    mismatches = []
    # Segment-population rows vs. borrower_360 re-aggregation must be
    # identical -- pure COUNT(*) after EXPLODE, no rounding or
    # filtering. Any drift is a refresh-ordering bug.
    for key, sp_n in sp.items():
        b_n = b360.get(key, 0)
        if sp_n != b_n:
            mismatches.append((key, sp_n, b_n))
    # Any keys present in b360 but not in segment_population are also drift.
    for key, b_n in b360.items():
        if key not in sp:
            mismatches.append((key, None, b_n))

    assert not mismatches, (
        f"segment_population vs borrower_360 drift (pop, b360): " f"{mismatches[:10]}"
    )


def test_overlay_segment_flags_match_membership(
    warehouse: tuple[str, str, str],
) -> None:
    """S1.3: every overlay segment's membership array entry must reconcile
    EXACTLY with its dedicated flag column on the same row. This pins the
    with_segments FILTER(ARRAY(...)) block against the flag predicates so a
    refactor cannot silently decouple the two, and it pins permit_activity
    at zero members until a true filed-permit source lands."""
    host, token, wh = warehouse
    if not _borrower_360_has_s13_columns(host, token, wh):
        pytest.skip(
            "gold.borrower_360 pre-dates the S1.3 overlay columns; run "
            "mip_refresh_scores from this branch before overlay reconciliation"
        )
    overlay_predicates = {
        "second_lien_itm": "second_lien_itm",
        "heloc_draw_to_payback": "has_heloc_draw_ending",
        "home_equity_history": "has_home_equity_history",
        "refi_propensity": "refi_propensity_heuristic >= 60",
        "itm_on_related_property": "itm_on_related_property",
        "payoff_loss_leads": "is_payoff_loss",
        "permit_activity": "has_permit",
    }
    drifts: list[str] = []
    for segment, predicate in overlay_predicates.items():
        rows = _run_sql(
            host,
            token,
            wh,
            f"""
            SELECT
              COUNT_IF(array_contains(segment_codes, '{segment}')) AS members,
              COUNT_IF({predicate}) AS flagged,
              COUNT_IF(array_contains(segment_codes, '{segment}')
                       AND NOT ({predicate})) AS members_without_flag
            FROM mip.gold.borrower_360
            """,
        )
        assert rows, f"{segment}: overlay reconciliation probe returned no rows"
        members, flagged, members_without_flag = (int(v) for v in rows[0])
        if members != flagged or members_without_flag != 0:
            drifts.append(
                f"{segment}: members={members} flagged={flagged} "
                f"members_without_flag={members_without_flag}"
            )
        if segment == "permit_activity" and members != 0:
            drifts.append(
                f"permit_activity has {members} members but no true filed-permit "
                "source exists -- membership must never be fabricated"
            )
    assert not drifts, "overlay segment flag/membership drift:\n" + "\n".join(drifts)


def test_lead_population_score_floor(warehouse: tuple[str, str, str]) -> None:
    """Every row in ``gold.lead_population`` must have
    opportunity_score >= 50 (the filter defined in
    sql/transformations/gold_lead_population.sql). This is a FLOOR check,
    not a count -- the current prod table has a legacy 10k row cap that
    the SQL file no longer imposes (freshness gap documented in
    docs/validation/segment-count-parity.md §5). The floor, however,
    MUST hold regardless of cap."""
    host, token, wh = warehouse
    rows = _run_sql(
        host,
        token,
        wh,
        """
        SELECT MIN(opportunity_score) AS min_score,
               COUNT(*) AS n
        FROM mip.gold.lead_population
        """,
    )
    assert rows, "lead_population returned no rows"
    min_score = int(rows[0][0])
    n = int(rows[0][1])
    assert min_score >= 50, (
        f"gold.lead_population has rows with opportunity_score={min_score} "
        f"< 50 ({n} rows total). The SQL predicate `WHERE opportunity_score "
        f">= 50` was bypassed or the refresh is corrupt."
    )
