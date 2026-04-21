"""Segment-count parity: raw Cotality share vs. `mip.gold.borrower_360`.

This is the highest-priority accuracy test for Slice 13. It answers the
question:

    When the app shows "Investor segment: 1,749,208 borrowers in the
    6-state footprint," does that number actually match what the raw
    Cotality Delta Share contains?

For each of the five UNBLOCKED segments:

    * ``itm``       -- rate_spread_bps >= 75 AND equity_pct >= 15
    * ``investor``  -- related_property_count >= 2 OR owner_is_corporate
                       OR is_absentee
    * ``equity``    -- equity_pct >= 35 AND second_pos_amount IS NULL
    * ``retention`` -- is_current_customer AND (rate_spread_bps >= 50
                       OR is_competitor_lien OR listed_for_sale)
    * ``listed`` / ``permit``   -- BLOCKED per data-contract §9; must be 0.

we compute a segment count per state from TWO independent paths:

    1. REFERENCE -- an INDEPENDENT query written against
       ``cotality_mortgage_data.corelogic.*`` that reimplements the
       segment rule from scratch (no silver/gold reuse). Independence is
       what makes this a validation rather than a tautology.
    2. GOLD      -- ``SELECT state, COUNT(*) FROM mip.gold.borrower_360
                     WHERE array_contains(segment_codes, '<segment>')``.

For the BLOCKED segments (listed, permit) the reference value is a hard
``0`` per the data-contract and we assert gold emits exactly 0.

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

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Constants -- the reference query reads the MORTGAGE30US fraction inline
# rather than joining against mip.silver.market_rates_weekly (which would
# re-use our silver code and defeat independence). This value was probed
# directly from the live silver table on 2026-04-21; re-probe if the FRED
# series ever moves and the silver `is_latest` row changes.
#
# Re-read rule: if you're updating this constant, first run
#   SELECT rate_fraction FROM mip.silver.market_rates_weekly
#   WHERE series_id='MORTGAGE30US' AND is_latest=TRUE
# and paste the exact decimal here.
# ---------------------------------------------------------------------------
MORTGAGE30US_FRACTION = 0.063  # 6.30% par rate

# Segment thresholds (must match docs/data-contract-module0.md §5 +
# sql/transformations/gold_borrower_360.sql lines 155-159)
MIN_SPREAD_BPS = 75
MIN_EQUITY_PCT = 15
HELOC_EQUITY_MIN = 35
RETENTION_MIN_SPREAD = 50

SIX_STATES = ("CA", "CO", "FL", "IL", "TX", "WA")
SEGMENTS = ("itm", "investor", "equity", "retention", "listed", "permit")

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


def _cli_token() -> tuple[str, str, str] | None:
    """Last-resort credential source: the Databricks CLI's OAuth token.

    Only consulted when env vars are not set. Requires ``databricks``
    on PATH and a configured profile whose host matches
    ``DATABRICKS_HOST`` below. Silently returns None if the CLI call
    fails.
    """
    profile = os.environ.get("DATABRICKS_CONFIG_PROFILE", "DEFAULT")
    try:
        out = subprocess.check_output(
            ["databricks", "auth", "token", "-p", profile],
            text=True,
            stderr=subprocess.DEVNULL,
            timeout=10,
        )
        token = json.loads(out).get("access_token")
    except (subprocess.CalledProcessError, FileNotFoundError, subprocess.TimeoutExpired, json.JSONDecodeError):
        return None
    if not token:
        return None
    # Still need host + warehouse from somewhere.
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
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
    if not host or not wh:
        return None
    return host, token, wh


def _creds() -> tuple[str, str, str] | None:
    host = os.environ.get("DATABRICKS_HOST") or os.environ.get(
        "DATABRICKS_SERVER_HOSTNAME"
    )
    token = os.environ.get("DATABRICKS_TOKEN")
    wh = os.environ.get("DATABRICKS_WAREHOUSE_ID")
    if host and token and wh:
        if not host.startswith("http"):
            host = "https://" + host
        return host.rstrip("/"), token, wh
    # Fallback to CLI OAuth.
    fallback = _cli_token()
    if fallback is None:
        return None
    host, token, wh = fallback
    if not host.startswith("http"):
        host = "https://" + host
    return host.rstrip("/"), token, wh


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


def _itm_reference_sql() -> str:
    """Reference ITM count per state.

    Mirrors gold_borrower_360.sql WHERE:
        * rate_spread_bps from fn_rate_spread(first_pos_rate, market)
          which is ROUND((current - market) * 10000) per
          sql/uc_functions/fn_rate_spread.sql.
        * equity_pct: GREATEST(0, LEAST(100, CASE
              WHEN estimated_combined_ltv > 0 THEN ROUND(100 - estimated_combined_ltv)
              WHEN estimated_value_mktg  > 0 THEN ROUND(100 * (avm - lien) / avm)
              ELSE 0 END))
        * Rate conversion: share rate is PERCENT (6.40 == 6.40%); divide
          by 100 before feeding to fn_rate_spread. Rates <= 0 -> NULL per
          silver rate-contract.
    """
    return f"""
    WITH src AS (
      SELECT
        situs_state AS state,
        clip,
        CASE
          WHEN first_position_mortgage_interest_rate IS NULL THEN NULL
          WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) <= 0 THEN NULL
          ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
        END AS rate_frac,
        CAST(estimated_value_mktg AS BIGINT) AS avm,
        CAST(total_amount_of_open_mortgage_liens AS BIGINT) AS lien,
        CAST(estimated_combined_ltv_loan_to_value AS DOUBLE) AS cltv
      FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
      WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
        AND clip IS NOT NULL
    ),
    calc AS (
      SELECT
        state, clip,
        CAST(ROUND((rate_frac - {MORTGAGE30US_FRACTION}) * 10000.0) AS INT) AS rate_spread_bps,
        CAST(GREATEST(0, LEAST(100, CASE
          WHEN cltv IS NOT NULL AND cltv > 0 THEN ROUND(100 - cltv)
          WHEN avm  IS NOT NULL AND avm  > 0 THEN ROUND(100.0 * (avm - COALESCE(lien, 0)) / avm)
          ELSE 0
        END)) AS INT) AS equity_pct
      FROM src
    )
    SELECT state, COUNT(*) AS n
    FROM calc
    WHERE rate_spread_bps >= {MIN_SPREAD_BPS} AND equity_pct >= {MIN_EQUITY_PCT}
    GROUP BY state ORDER BY state
    """


def _equity_reference_sql() -> str:
    """Reference equity-segment count per state.

    Segment rule (gold_borrower_360.sql line 185):
        equity_pct >= 35 AND second_pos_amount IS NULL
    """
    return f"""
    WITH src AS (
      SELECT
        situs_state AS state,
        clip,
        CAST(estimated_value_mktg AS BIGINT) AS avm,
        CAST(total_amount_of_open_mortgage_liens AS BIGINT) AS lien,
        CAST(estimated_combined_ltv_loan_to_value AS DOUBLE) AS cltv,
        CAST(second_position_mortgage_amount AS BIGINT) AS second_pos
      FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
      WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
        AND clip IS NOT NULL
    ),
    calc AS (
      SELECT
        state, clip, second_pos,
        CAST(GREATEST(0, LEAST(100, CASE
          WHEN cltv IS NOT NULL AND cltv > 0 THEN ROUND(100 - cltv)
          WHEN avm  IS NOT NULL AND avm  > 0 THEN ROUND(100.0 * (avm - COALESCE(lien, 0)) / avm)
          ELSE 0
        END)) AS INT) AS equity_pct
      FROM src
    )
    SELECT state, COUNT(*) AS n
    FROM calc
    WHERE equity_pct >= {HELOC_EQUITY_MIN} AND second_pos IS NULL
    GROUP BY state ORDER BY state
    """


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
      WHERE situs_state IN ('IL','CA','FL','TX','WA','CO') AND clip IS NOT NULL
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
    WHERE l.situs_state IN ('IL','CA','FL','TX','WA','CO') AND l.clip IS NOT NULL
      AND (COALESCE(b.related_n, 1) >= 2 OR p.is_corp OR p.is_absentee)
    GROUP BY p.state ORDER BY p.state
    """


def _retention_reference_sql() -> str:
    """Reference retention-segment count per state.

    Segment rule:
        is_current_customer AND
          (rate_spread_bps >= 50 OR is_competitor_lien OR listed_for_sale)

    Independent derivation notes:
      * is_current_customer: UPPER(first_position_currently_assigned_
        lender_company_name) LIKE '%SUMMIT%' (Summit Mortgage is the
        sample-lender per CLAUDE.md naming rules).
      * is_competitor_lien: servicer known AND NOT contains SUMMIT --
        which makes is_current_customer and is_competitor_lien
        mutually exclusive by construction. So the OR-branch collapses
        to: is_current_customer AND rate_spread_bps >= 50 (listed_for_sale
        is BLOCKED -> FALSE).
    """
    return f"""
    WITH calc AS (
      SELECT
        situs_state AS state,
        (first_position_currently_assigned_lender_company_name IS NOT NULL
         AND UPPER(first_position_currently_assigned_lender_company_name) LIKE '%SUMMIT%')
          AS is_summit,
        CAST(ROUND((
          CASE
            WHEN first_position_mortgage_interest_rate IS NULL THEN NULL
            WHEN CAST(first_position_mortgage_interest_rate AS DOUBLE) <= 0 THEN NULL
            ELSE CAST(first_position_mortgage_interest_rate AS DOUBLE) / 100.0
          END - {MORTGAGE30US_FRACTION}) * 10000.0
        ) AS INT) AS spread_bps
      FROM cotality_mortgage_data.corelogic.entrada_eval_voluntary_lien_status_marketing_v2
      WHERE situs_state IN ('IL','CA','FL','TX','WA','CO') AND clip IS NOT NULL
    )
    SELECT state, COUNT(*) AS n
    FROM calc
    WHERE is_summit AND spread_bps >= {RETENTION_MIN_SPREAD}
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


@pytest.fixture(scope="module")
def counts(warehouse: tuple[str, str, str]) -> dict[str, dict[str, dict[str, int]]]:
    """Returns ``counts[segment][path][state] -> int``.

    path is 'ref' or 'gold'. States are the six-state footprint.
    BLOCKED segments (listed, permit) have ref = {state: 0, ...} and
    gold = whatever the warehouse emits (should also be all zero).
    """
    host, token, wh = warehouse

    def zero_map() -> dict[str, int]:
        return {s: 0 for s in SIX_STATES}

    reference_sqls: dict[str, str] = {
        "itm": _itm_reference_sql(),
        "equity": _equity_reference_sql(),
        "investor": _investor_reference_sql(),
        "retention": _retention_reference_sql(),
        # BLOCKED segments: reference is definitionally 0 per data-contract §9.
    }

    out: dict[str, dict[str, dict[str, int]]] = {}
    for seg in SEGMENTS:
        ref = zero_map()
        if seg in reference_sqls:
            t0 = time.time()
            rows = _run_sql(host, token, wh, reference_sqls[seg])
            logger.debug("REF %s (%.1fs): %s", seg, time.time() - t0, rows)
            ref.update(_rows_to_state_map(rows))
        gold_rows = _run_sql(host, token, wh, _gold_segment_sql(seg))
        logger.debug("GOLD %s: %s", seg, gold_rows)
        gold = zero_map()
        gold.update(_rows_to_state_map(gold_rows))
        out[seg] = {"ref": ref, "gold": gold}
    return out


# ---------------------------------------------------------------------------
# Parametrized segment*state parity test.
# ---------------------------------------------------------------------------


_PARAMS = [(seg, state) for seg in SEGMENTS for state in SIX_STATES]


@pytest.mark.parametrize(
    ("segment", "state"),
    _PARAMS,
    ids=[f"{seg}-{st}" for seg, st in _PARAMS],
)
def test_segment_count_parity(
    counts: dict[str, dict[str, dict[str, int]]],
    segment: str,
    state: str,
) -> None:
    """One assertion per (segment, state). Failure message prints both
    counts and the tolerance branch taken for triage."""
    ref_n = counts[segment]["ref"][state]
    gold_n = counts[segment]["gold"][state]

    # BLOCKED segments: must be exactly 0 on BOTH sides. A non-zero
    # gold count here means either the gold CTAS stopped hardcoding
    # listed_for_sale/has_permit = FALSE, OR the BLOCKED contract
    # changed. Either way, surface loudly.
    if segment in ("listed", "permit"):
        assert ref_n == 0, (
            f"{segment}/{state}: reference must be 0 for BLOCKED segment"
        )
        assert gold_n == 0, (
            f"{segment}/{state}: gold emitted {gold_n} rows for BLOCKED "
            f"segment (expected 0). Check gold_borrower_360.sql "
            f"hardcoded has_permit/listed_for_sale = FALSE."
        )
        return

    delta = abs(ref_n - gold_n)
    # Small-N branch: exact match required.
    if ref_n < ABS_TOLERANCE_MIN or gold_n < ABS_TOLERANCE_MIN:
        assert ref_n == gold_n, (
            f"{segment}/{state}: ref={ref_n} gold={gold_n} "
            f"delta={delta} (exact match required below {ABS_TOLERANCE_MIN})"
        )
        return
    # Large-N branch: 0.5% relative tolerance.
    pct = delta / max(ref_n, 1)
    assert pct <= REL_TOLERANCE, (
        f"{segment}/{state}: ref={ref_n} gold={gold_n} "
        f"delta={delta} ({pct * 100:.2f}% > {REL_TOLERANCE * 100}%)"
    )


# ---------------------------------------------------------------------------
# Bonus parity checks: total row count + segment_population consistency.
# ---------------------------------------------------------------------------


def test_borrower_360_total_rows(warehouse: tuple[str, str, str]) -> None:
    """``gold.borrower_360`` row count must equal the 6-state share row
    count (exact -- no aggregation, just a filter+join). A drift here
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
           WHERE situs_state IN ('IL','CA','FL','TX','WA','CO')
             AND clip IS NOT NULL) AS share_6
        """,
    )
    assert rows, "no rows returned for total-count probe"
    b360, share_6 = int(rows[0][0]), int(rows[0][1])
    assert b360 == share_6, (
        f"gold.borrower_360={b360:,} but share(6-state)={share_6:,} "
        f"-- drift of {abs(b360 - share_6)} rows. Check gold_borrower_"
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
        f"segment_population vs borrower_360 drift (pop, b360): "
        f"{mismatches[:10]}"
    )


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
