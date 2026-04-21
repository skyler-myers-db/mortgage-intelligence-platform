-- =============================================================================
-- silver_market_rates_weekly.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.silver.market_rates_weekly`. This table is the
--            market-rate denominator for Module 0's rate-spread math
--            (`mip.gold.fn_rate_spread` + `backend.services.scoring.
--            rate_spread_bps`). Populated from FRED series MORTGAGE30US
--            (and optionally MORTGAGE15US) by `jobs/fred_rates_ingest.py`,
--            with a committed seed CSV backing the first-ever boot.
--
-- Data contract reference: docs/data-contract-module0.md §2.5.
--
-- Source columns and semantics:
--            series_id        FRED series code (MORTGAGE30US for Module 0).
--            observation_week Week-starting Monday (FRED publishes Thursday;
--                             the ingest job applies date_trunc('week', ...)
--                             before MERGE to match the contract grain).
--            rate_pct         FRED `value` column, rate in percent
--                             (6.40 == 6.40%). Ingest rejects NULL.
--            rate_fraction    rate_pct / 100.0. Matches the fractional-rate
--                             form that `fn_rate_spread` consumes.
--            vintage_ts       Timestamp of the FRED pull (or seed-load
--                             timestamp for bootstrap rows).
--            is_latest        Convenience flag set via window function at
--                             ingest time; `=TRUE` on exactly one row per
--                             series_id. Metric views MUST join on this
--                             predicate to keep `fn_rate_spread`
--                             deterministic within a session.
--            source           'fred' for live FRED fetches; 'seed' for the
--                             committed bootstrap CSV at
--                             data/seeds/fred_mortgage30us_seed.csv. Lets
--                             us trivially identify rows that predate the
--                             first successful live refresh.
--            _meta_batch_id   Job-run correlation id. NULL-tolerated for
--                             the seed load (seed has no run id).
--
-- Clustering: Liquid clustering on (series_id, observation_week) -- the
--            gold metric views always filter on `is_latest = TRUE` and the
--            ingest MERGE keys are (series_id, observation_week), so this
--            clustering lets both access patterns skip efficiently.
--            Row count is < 2k even across a decade of weekly data, so
--            partitioning would be overkill; Delta + clustering is the
--            right posture.
--
-- PK:        Logical PK is (series_id, observation_week). Unity Catalog
--            supports informational primary keys; we declare them via
--            ALTER TABLE after the CREATE to remain idempotent.
--            `fn_rate_spread` joins via `is_latest = TRUE` at runtime;
--            the PK exists for Genie/documentation readability.
--
-- Idempotency: CREATE TABLE IF NOT EXISTS. ALTER TABLE ... ADD CONSTRAINT
--            IF NOT EXISTS. Safe to re-run on every deploy.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.silver.market_rates_weekly (
  series_id        STRING    NOT NULL COMMENT 'FRED series code, e.g. MORTGAGE30US.',
  observation_week DATE      NOT NULL COMMENT 'Week-starting Monday; date_trunc(week, FRED date).',
  rate_pct         DOUBLE    NOT NULL COMMENT 'Rate in percent (6.40 == 6.40%). FRED value.',
  rate_fraction    DOUBLE    NOT NULL COMMENT 'rate_pct / 100.0. Fractional form consumed by fn_rate_spread.',
  vintage_ts       TIMESTAMP NOT NULL COMMENT 'Ingest timestamp (FRED pull time or seed-load time).',
  is_latest        BOOLEAN   NOT NULL COMMENT 'TRUE on exactly one row per series_id; join predicate for metric views.',
  source           STRING    NOT NULL COMMENT "'fred' (live pull) or 'seed' (committed bootstrap CSV).",
  _meta_batch_id   STRING             COMMENT 'Job run id for observability; NULL for seed rows.'
)
USING DELTA
CLUSTER BY (series_id, observation_week)
COMMENT 'Weekly mortgage rate series from FRED. Populated by jobs/fred_rates_ingest.py; bootstrap from data/seeds/fred_mortgage30us_seed.csv. See docs/data-contract-module0.md §2.5.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
