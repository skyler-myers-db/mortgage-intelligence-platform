-- =============================================================================
-- gold_rate_window_weekly.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.rate_window_weekly` -- the "why now" rate
--            window behind the Analytics Executive tab (2026-09-21 UI/UX
--            audit, dataviz-08 / dataviz-06). One row per FRED MORTGAGE30US
--            week carrying that week's market rate, the CURRENT fixed-rate
--            book's note-rate band (p25 / median / p75), and the number of
--            liens that would clear the in-the-money screen at that week's
--            rate under the governed thresholds.
--
-- Grain:     One row per (series_id, observation_week). PK observation_week
--            for the single MORTGAGE30US series.
-- Clustering: Liquid cluster on observation_week. The only read pattern is
--            "the whole series ordered by week" (< 300 rows), so clustering
--            is nominal; it keeps the table shape consistent with its gold
--            siblings.
--
-- Semantics: "Today's book against the historical rate." The book
--            distribution is measured ONCE at the refresh anchor and applied
--            to every week; it is NOT the book as it stood in that week.
--            `book_as_of` says so on every row so no consumer can mistake
--            the series for a true portfolio history.
--
-- Book:      gold.borrower_360 rows with an ACTIVE first lien
--            (current_rate > 0, the same gate rate_spread_bps applies) whose
--            silver.lien_current.first_pos_rate_type is FIX and whose bounded
--            rate sits strictly inside the 1%..15% clamp (a rate AT a bound is
--            a clamp artifact and mints no spread economics -- identical to
--            the borrower_360 rule). ARM and unknown rate types are excluded
--            because an adjustable coupon is not comparable to the 30-year
--            fixed market print.
--
-- ITM rule:  itm_count reuses the exact canonical primitives --
--            mip.gold.fn_in_the_money(mip.gold.fn_rate_spread(note_rate,
--            market_rate_fraction), equity_pct, min_spread_bps_applied,
--            min_equity_pct_applied) -- with the per-refresh thresholds
--            carried on borrower_360. The rule is reused, never forked, so
--            the current week's itm_count is the fixed-rate subset of the
--            headline in-the-money count.
--
-- Cost:      Built once per gold refresh by mip_refresh_scores. The app never
--            runs a percentile over the multi-million-row book per request;
--            it reads this table.
--
-- Data contract reference: docs/data-contract-module0.md §3 (rate window).
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.rate_window_weekly (
  series_id               STRING    NOT NULL COMMENT 'FRED series code carried from silver.market_rates_weekly; MORTGAGE30US for Module 0.',
  observation_week        DATE      NOT NULL COMMENT 'Week-starting Monday carried from silver.market_rates_weekly.observation_week. PK.',
  market_rate_pct         DOUBLE    NOT NULL COMMENT 'FRED 30-year fixed rate for the week in percent (6.30 == 6.30%).',
  market_rate_fraction    DOUBLE    NOT NULL COMMENT 'market_rate_pct / 100.0; the fractional form fn_rate_spread consumed for itm_count.',
  is_latest               BOOLEAN   NOT NULL COMMENT 'TRUE on the most recent week per series, carried from silver.market_rates_weekly.is_latest; this is the current market print.',
  book_median_rate_pct    DOUBLE             COMMENT 'Median note rate of the current fixed-rate book in percent (percentile_approx, accuracy 10000). NULL only when the book is empty.',
  book_p25_rate_pct       DOUBLE             COMMENT '25th-percentile note rate of the current fixed-rate book in percent (percentile_approx, accuracy 10000). NULL only when the book is empty.',
  book_p75_rate_pct       DOUBLE             COMMENT '75th-percentile note rate of the current fixed-rate book in percent (percentile_approx, accuracy 10000). NULL only when the book is empty.',
  book_lien_count         BIGINT    NOT NULL COMMENT 'Active fixed-rate first liens in the current book: gold.borrower_360 rows with current_rate > 0 whose silver.lien_current.first_pos_rate_type is FIX and whose bounded rate is strictly inside the 1%..15% clamp. The same value on every week.',
  itm_count               BIGINT    NOT NULL COMMENT 'Liens in the money at THIS week market rate: fn_in_the_money(fn_rate_spread(note_rate, market_rate_fraction), equity_pct, min_spread_bps_applied, min_equity_pct_applied) summed over the current fixed-rate book. Same primitives and thresholds as gold.lead_scores.in_the_money.',
  min_spread_bps_applied  INT                COMMENT 'Rate-spread threshold (bps) applied to every week, carried from gold.borrower_360.min_spread_bps_applied. NULL only when the book is empty.',
  min_equity_pct_applied  INT                COMMENT 'Equity threshold (pct) applied to every week, carried from gold.borrower_360.min_equity_pct_applied. NULL only when the book is empty.',
  book_as_of              TIMESTAMP NOT NULL COMMENT 'Refresh anchor of the book distribution: every week is the CURRENT book measured against that week historical rate, not the book as it stood that week.',
  refreshed_at            TIMESTAMP NOT NULL COMMENT 'Deterministic refresh anchor from mip.ref.refresh_run_state.'
)
USING DELTA
CLUSTER BY (observation_week)
COMMENT 'Why-now rate window: one row per FRED MORTGAGE30US week with that week market rate, the current fixed-rate book note-rate band (p25 / median / p75 via percentile_approx), and the count in the money at that week rate under the governed fn_rate_spread / fn_in_the_money rule. The book is as-of the refresh anchor (book_as_of) and applied to every week. Built by mip_refresh_scores via gold_rate_window_weekly.sql; read by /api/v1/analytics/rate-window.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
