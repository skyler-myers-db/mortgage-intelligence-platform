-- =============================================================================
-- assert_scheduled_refresh_outcome.sql  (mip_scheduled_refresh leaf gate)
-- -----------------------------------------------------------------------------
-- Purpose:   Fail the weekly chain loudly when
--            (a) silver.market_rates_weekly does not hold exactly one
--                is_latest MORTGAGE30US row;
--            (b) gold.borrower_360 scored against a rate other than that
--                row (audit C1 stale-rate skew; the same comparison as
--                tools/databricks/verify_market_rate_alignment.py, tolerance
--                1e-9, here on both MIN and MAX so a mixed table fails too);
--            (c) that row is not a FRED print inside the 14-day window
--                gold_source_readiness.sql uses for FRED 'live'.
--
-- SELECT-only: no DDL, no DML. It depends on the gold task (ALL_SUCCESS), so
-- a failed or skipped gold run already fails the job without a wall-clock
-- check here. Every value is aggregated in a one-row CTE, so a duplicate
-- is_latest row reaches the named RAISE_ERROR instead of a scalar-subquery
-- error, and every NULL fails closed (a NULL comparison would otherwise fall
-- through to ELSE 1).
--
-- tests/unit/test_scheduled_refresh_outcome_sql.py pins this file.
-- =============================================================================

WITH latest_rate AS (
  SELECT
    COUNT(*) AS latest_rows,
    MAX(rate_fraction) AS rate_fraction,
    MAX(observation_week) AS observation_week,
    MAX(source) AS source
  FROM mip.silver.market_rates_weekly
  WHERE series_id = 'MORTGAGE30US' AND is_latest = TRUE
),
gold_rate AS (
  SELECT
    MIN(market_rate_fraction) AS min_rate_fraction,
    MAX(market_rate_fraction) AS max_rate_fraction
  FROM mip.gold.borrower_360
)
SELECT
  CASE
    WHEN l.latest_rows <> 1
      THEN RAISE_ERROR('assert_scheduled_refresh_outcome: expected exactly one is_latest MORTGAGE30US row in silver.market_rates_weekly.')
    WHEN l.rate_fraction IS NULL
      OR g.min_rate_fraction IS NULL
      OR g.max_rate_fraction IS NULL
      OR ABS(g.max_rate_fraction - l.rate_fraction) > 1e-9
      OR ABS(g.min_rate_fraction - l.rate_fraction) > 1e-9
      THEN RAISE_ERROR('assert_scheduled_refresh_outcome: gold.borrower_360 market rate differs from silver is_latest MORTGAGE30US (stale-rate skew).')
    WHEN COALESCE(l.source, '') <> 'fred'
      OR l.observation_week IS NULL
      OR l.observation_week < current_date() - INTERVAL 14 DAYS
      THEN RAISE_ERROR('assert_scheduled_refresh_outcome: the is_latest MORTGAGE30US row is not a FRED print within 14 days.')
    ELSE 1
  END AS ok
FROM latest_rate AS l CROSS JOIN gold_rate AS g;
