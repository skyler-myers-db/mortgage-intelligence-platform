-- =============================================================================
-- gold_rate_sensitivity_rollup.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.rate_sensitivity_rollup` -- the Rate Lever's
--            precomputed scenario grid (2026-09-21 UI/UX audit, wow-stage-1).
--            One row per state per par-rate step: how many of the state's
--            addressable borrowers would clear this refresh's refi screen if
--            the 30-year par rate moved by `step_bps`.
--
-- Grain:     One row per (state, step_bps). About 51 states x 9 steps
--            (-100 .. +100 bps in 25 bps steps). PK (state, step_bps).
-- Clustering: Liquid cluster on (state), like the sibling geography rollups.
--
-- Population: the map's addressable definition -- every gold.borrower_360 row
--            with a state -- LEFT JOINed to silver.lien_current on CLIP. The
--            note rate passes borrower_360's active-lien and clamp gate; a
--            gated row (no active lien, a rate AT the 1% / 15% clamp, no lien
--            row) stays in the population with a NULL note, which
--            fn_rate_spread scores as the no-signal 0 at every step, so the
--            scenario never moves it. `rate_movable_borrowers` counts only
--            the rows with a note.
--
-- Rule:      Each step is a different market rate, never a shift of rounded
--            spread bins:
--              fn_in_the_money(fn_rate_spread(note_rate_fraction,
--                market_rate_fraction + CAST(step_bps AS DOUBLE)
--                                     / CAST(10000 AS DOUBLE)),
--                equity_pct, min_spread_bps_applied, min_equity_pct_applied)
--            The base par is borrower_360.market_rate_fraction (the par the
--            refresh scored with), so step 0 equals the scored in_the_money
--            count even when FRED lands between refreshes.
--
-- Semantics: A scenario, not a forecast. Contactable counts are NOT a column:
--            the contactability predicate reads CURRENT_TIMESTAMP and has one
--            owner (backend/services/eligibility.py), so the endpoint joins a
--            live eligible aggregate onto these rows per request.
--
-- Cost:      Built once per gold refresh by mip_refresh_scores
--            (ctas_rate_sensitivity_rollup). Never on a schedule of its own.
--
-- Contract:  pinned by tests/unit/test_rate_sensitivity_sql_contract.py and,
--            against real UC, tests/integration/test_rate_sensitivity_parity.py.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.rate_sensitivity_rollup (
  state                          STRING    NOT NULL COMMENT '2-char USPS state code (uppercase), carried from gold.borrower_360.state. PK part.',
  step_bps                       INT       NOT NULL COMMENT 'Par-rate move in basis points (-100 .. +100, 25 bps steps); positive = par rises. PK part.',
  base_market_rate_fraction      DOUBLE             COMMENT 'The par rate this refresh scored with (gold.borrower_360.market_rate_fraction), as a fraction. NULL only when the refresh had no market print.',
  scenario_market_rate_fraction  DOUBLE             COMMENT 'base_market_rate_fraction + step_bps / 10000 (DOUBLE arithmetic): the par rate this step describes, as a fraction.',
  scenario_market_rate_pct       DOUBLE             COMMENT 'scenario_market_rate_fraction in percent (6.30 == 6.30%), rounded to 6 places for display.',
  addressable_borrowers          BIGINT    NOT NULL COMMENT 'gold.borrower_360 rows in the state: the map addressable count. The same on every step.',
  rate_movable_borrowers         BIGINT    NOT NULL COMMENT 'Addressable borrowers with a gated note rate (active lien, bounded rate strictly inside 1%..15%): the only ones a scenario can move.',
  in_the_money_borrowers         BIGINT    NOT NULL COMMENT 'Borrowers that clear the refi screen at this step: fn_in_the_money(fn_rate_spread(note, par + step / 10000), equity_pct, thresholds). Step 0 equals SUM(borrower_360.in_the_money).',
  min_spread_bps_applied         INT                COMMENT 'Rate-spread threshold (bps) of this refresh, carried from gold.borrower_360.min_spread_bps_applied.',
  min_equity_pct_applied         INT                COMMENT 'Equity threshold (pct) of this refresh, carried from gold.borrower_360.min_equity_pct_applied.',
  book_as_of                     TIMESTAMP NOT NULL COMMENT 'Refresh anchor of the book the grid was measured over (mip.ref.refresh_run_state).',
  refreshed_at                   TIMESTAMP NOT NULL COMMENT 'Deterministic refresh anchor from mip.ref.refresh_run_state.'
)
USING DELTA
CLUSTER BY (state)
COMMENT 'Rate Lever scenario grid: per state and par-rate step (-100 .. +100 bps), the addressable borrowers that would clear this refresh refi screen under the canonical fn_rate_spread / fn_in_the_money rule re-run at par + step. A scenario, not a forecast. Built by mip_refresh_scores via gold_rate_sensitivity_rollup.sql; read by /api/v1/geo/rate-sensitivity.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
