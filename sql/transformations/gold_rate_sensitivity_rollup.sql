-- =============================================================================
-- gold_rate_sensitivity_rollup.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.rate_sensitivity_rollup` via CTAS: the Rate
--            Lever's scenario grid (2026-09-21 UI/UX audit, wow-stage-1). One
--            row per (state, step_bps): the addressable borrowers that would
--            clear this refresh's refi screen if the 30-year par rate moved by
--            step_bps. A scenario, not a forecast.
--
-- Grain:     One row per (state, step_bps); step_bps in the grid
--            -100, -75, -50, -25, 0, 25, 50, 75, 100
--            (backend/services/rate_scenario.RATE_SCENARIO_STEPS_BPS; the SQL
--            contract test pins the literal below to it).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent full rebuild.
--            Runs after assert_borrower_360_fresh in mip_refresh_scores and
--            parallelizes with the sibling rollups.
--
-- Population: every gold.borrower_360 row with a state (the map's addressable
--            definition), LEFT JOINed to silver.lien_current on CLIP. The note
--            rate passes borrower_360's own active-lien and clamp gate
--            (NOTE_RATE_GATE_SQL in backend/services/rate_scenario.py, embedded
--            verbatim): current_rate > 0 and a bounded rate strictly inside
--            1%..15%. A gated row is NOT dropped: it keeps a NULL note, which
--            fn_rate_spread scores as the no-signal 0 at every step, so the
--            scenario never moves it and step 0 still equals borrower_360
--            (even under a min_spread_bps <= 0 screen).
--
-- Rule:      REUSED, never forked (SCENARIO_ITM_SQL, embedded verbatim): each
--            step is a different market rate,
--              fn_in_the_money(fn_rate_spread(note, par + step / 10000), ...)
--            with the step divided in DOUBLE arithmetic. Never by shifting
--            rounded spread bins: fn_rate_spread BROUNDs half-even, so a
--            shifted bin disagrees with the direct evaluation whenever the raw
--            spread sits on a half basis point (tests/fixtures/
--            rate_scenario_golden.json). The base par is
--            borrower_360.market_rate_fraction, the par the refresh scored
--            with, so step 0 equals SUM(borrower_360.in_the_money) even when
--            FRED lands between refreshes.
--
-- Cost:      The book is first collapsed LOSSLESSLY to (state, note, equity,
--            thresholds, par) cells with counts -- the rule reads nothing
--            else -- as gold_rate_window_weekly does, then the cells are
--            crossed with the nine steps. Both primitives are SQL UDFs that
--            Databricks inlines, so each evaluation is scalar arithmetic.
--
-- Contactable counts are deliberately absent: the eligibility predicate reads
-- CURRENT_TIMESTAMP and has one owner (backend/services/eligibility.py), so
-- the endpoint joins a live eligible aggregate onto these rows per request.
--
-- CTAS re-declares clustering/comments/properties because COR TABLE drops
-- DDL metadata on every refresh (2026-06-11 audit P2-8). Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_rate_sensitivity_rollup.sql.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.rate_sensitivity_rollup
CLUSTER BY (state)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH refresh_anchor AS (
  -- Shared refresh_at captured once per run. See audit-holes-round-3 #7.
  SELECT refresh_at
  FROM mip.ref.refresh_run_state
  ORDER BY captured_at DESC
  LIMIT 1
),
book AS (
  -- Every addressable borrower. LEFT JOIN: a CLIP without a lien row stays
  -- in the population with a NULL note (the gate's CASE has no ELSE).
  SELECT
    b.state,
    CASE WHEN b.current_rate > 0
          AND mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) > 0.01
          AND mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) < 0.15
         THEN mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) END AS note_rate_fraction,
    b.equity_pct,
    b.min_spread_bps_applied,
    b.min_equity_pct_applied,
    b.market_rate_fraction
  FROM mip.gold.borrower_360 AS b
  LEFT JOIN mip.silver.lien_current AS lc
    ON lc.clip = b.clip
  WHERE b.state IS NOT NULL
),
book_cells AS (
  -- Lossless collapse for the rule: fn_rate_spread reads the note and the
  -- par; fn_in_the_money reads the spread, equity_pct and the thresholds.
  SELECT
    state,
    note_rate_fraction,
    equity_pct,
    min_spread_bps_applied,
    min_equity_pct_applied,
    market_rate_fraction,
    COUNT(*) AS borrower_count
  FROM book
  GROUP BY state, note_rate_fraction, equity_pct, min_spread_bps_applied,
           min_equity_pct_applied, market_rate_fraction
),
grid AS (
  SELECT EXPLODE(ARRAY(-100, -75, -50, -25, 0, 25, 50, 75, 100)) AS step_bps
),
scenario AS (
  SELECT
    k.state,
    g.step_bps,
    MAX(k.market_rate_fraction)   AS base_market_rate_fraction,
    -- Distinct aliases: the rule below must read the CELL columns, never a
    -- lateral alias of this SELECT list.
    MAX(k.min_spread_bps_applied) AS spread_threshold_bps,
    MAX(k.min_equity_pct_applied) AS equity_threshold_pct,
    SUM(k.borrower_count)         AS addressable_borrowers,
    SUM(CASE WHEN k.note_rate_fraction IS NOT NULL THEN k.borrower_count ELSE 0 END)
                                  AS rate_movable_borrowers,
    SUM(
      CASE
        WHEN mip.gold.fn_in_the_money(
               mip.gold.fn_rate_spread(
                 note_rate_fraction,
                 market_rate_fraction + CAST(step_bps AS DOUBLE) / CAST(10000 AS DOUBLE)
               ),
               equity_pct,
               min_spread_bps_applied,
               min_equity_pct_applied
             )
        THEN k.borrower_count
        ELSE 0
      END
    )                             AS in_the_money_borrowers
  FROM book_cells AS k
  CROSS JOIN grid AS g
  GROUP BY k.state, g.step_bps
)
SELECT
  s.state,
  CAST(s.step_bps AS INT)                                              AS step_bps,
  CAST(s.base_market_rate_fraction AS DOUBLE)                          AS base_market_rate_fraction,
  CAST(s.base_market_rate_fraction + CAST(s.step_bps AS DOUBLE) / CAST(10000 AS DOUBLE) AS DOUBLE)
                                                                       AS scenario_market_rate_fraction,
  CAST(ROUND((s.base_market_rate_fraction + CAST(s.step_bps AS DOUBLE) / CAST(10000 AS DOUBLE)) * 100, 6) AS DOUBLE)
                                                                       AS scenario_market_rate_pct,
  CAST(s.addressable_borrowers AS BIGINT)                              AS addressable_borrowers,
  CAST(s.rate_movable_borrowers AS BIGINT)                             AS rate_movable_borrowers,
  CAST(s.in_the_money_borrowers AS BIGINT)                             AS in_the_money_borrowers,
  CAST(s.spread_threshold_bps AS INT)                                  AS min_spread_bps_applied,
  CAST(s.equity_threshold_pct AS INT)                                  AS min_equity_pct_applied,
  (SELECT refresh_at FROM refresh_anchor)                              AS book_as_of,
  (SELECT refresh_at FROM refresh_anchor)                              AS refreshed_at
FROM scenario AS s;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh. COMMENT ON
-- COLUMN keeps the asset-page comments refresh-stable; the SQL file task
-- executes the statements in order.
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.state IS '2-char USPS state code (uppercase), carried from gold.borrower_360.state. PK part.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.step_bps IS 'Par-rate move in basis points (-100 .. +100, 25 bps steps); positive = par rises. PK part.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.base_market_rate_fraction IS 'The par rate this refresh scored with (gold.borrower_360.market_rate_fraction), as a fraction. NULL only when the refresh had no market print.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.scenario_market_rate_fraction IS 'base_market_rate_fraction + step_bps / 10000 (DOUBLE arithmetic): the par rate this step describes, as a fraction.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.scenario_market_rate_pct IS 'scenario_market_rate_fraction in percent (6.30 == 6.30%), rounded to 6 places for display.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.addressable_borrowers IS 'gold.borrower_360 rows in the state: the map addressable count. The same on every step.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.rate_movable_borrowers IS 'Addressable borrowers with a gated note rate (active lien, bounded rate strictly inside 1%..15%): the only ones a scenario can move.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.in_the_money_borrowers IS 'Borrowers that clear the refi screen at this step: fn_in_the_money(fn_rate_spread(note, par + step / 10000), equity_pct, thresholds). Step 0 equals SUM(borrower_360.in_the_money).';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.min_spread_bps_applied IS 'Rate-spread threshold (bps) of this refresh, carried from gold.borrower_360.min_spread_bps_applied.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.min_equity_pct_applied IS 'Equity threshold (pct) of this refresh, carried from gold.borrower_360.min_equity_pct_applied.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.book_as_of IS 'Refresh anchor of the book the grid was measured over (mip.ref.refresh_run_state).';
COMMENT ON COLUMN mip.gold.rate_sensitivity_rollup.refreshed_at IS 'Deterministic refresh anchor from mip.ref.refresh_run_state.';
