-- =============================================================================
-- gold_rate_window_weekly.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.rate_window_weekly` via CTAS: one row per
--            FRED MORTGAGE30US week carrying that week's market rate, the
--            CURRENT fixed-rate book's note-rate band (p25 / median / p75),
--            and the number of liens that clear the in-the-money screen at
--            that week's rate. Backs the Analytics Executive "Why now"
--            surface (2026-09-21 UI/UX audit, dataviz-08 / dataviz-06).
--
-- Grain:     One row per (series_id, observation_week).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent full rebuild;
--            matches the sibling gold CTAS posture. Runs after
--            assert_borrower_360_fresh in the mip_refresh_scores job because
--            the book is read from gold.borrower_360.
--
-- Semantics: "Today's book against the historical rate." The book is
--            measured ONCE at the refresh anchor and applied to every week.
--            `book_as_of` carries that anchor on every row.
--
-- Book:      gold.borrower_360 rows with an ACTIVE first lien
--            (current_rate > 0 -- borrower_360 zeroes current_rate when the
--            amortized balance is paid down, the same gate rate_spread_bps
--            applies), joined to silver.lien_current on CLIP for the rate
--            type (FIX only) and the bounded note rate. A bounded rate AT the
--            1% / 15% clamp is a clamp artifact and is excluded, exactly as
--            borrower_360 excludes it from rate_spread_bps.
--
-- ITM rule:  REUSED, never forked. Per week:
--              fn_in_the_money(fn_rate_spread(note_rate, market_rate_fraction),
--                              equity_pct, min_spread_bps_applied,
--                              min_equity_pct_applied)
--            with the per-refresh thresholds carried on borrower_360. The
--            book is first collapsed to (note_rate, equity_pct, thresholds)
--            cells with counts, so the weeks x cells evaluation is at most a
--            few million UDF calls per refresh instead of weeks x 5M rows.
--            Collapsing is lossless for the rule: both primitives read only
--            those four inputs.
--
-- Band:      percentile_approx(note_rate_fraction, ARRAY(0.25, 0.5, 0.75),
--            10000) over the ungrouped book. The accuracy parameter bounds
--            the rank error to 1/10000, far below the basis-point grain of
--            a note rate, and the column comments disclose the estimator.
--
-- Cost:      Once per gold refresh. The app reads this table and never runs
--            a per-request percentile over the multi-million-row book.
--
-- CTAS re-declares clustering/comments/properties because COR TABLE drops
-- DDL metadata on every refresh (2026-06-11 audit P2-8). Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_rate_window_weekly.sql.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.rate_window_weekly
CLUSTER BY (observation_week)
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
book_raw AS (
  -- The current book: one row per active fixed-rate first lien. The bounded
  -- rate is computed once here so the clamp gate below reads the same value
  -- the ITM cells and the band read.
  SELECT
    mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) AS note_rate_fraction,
    b.equity_pct,
    b.min_spread_bps_applied,
    b.min_equity_pct_applied
  FROM mip.gold.borrower_360 AS b
  JOIN mip.silver.lien_current AS lc
    ON lc.clip = b.clip
  WHERE b.current_rate > 0
    AND UPPER(TRIM(lc.first_pos_rate_type)) = 'FIX'
),
book AS (
  SELECT
    note_rate_fraction,
    equity_pct,
    min_spread_bps_applied,
    min_equity_pct_applied
  FROM book_raw
  WHERE note_rate_fraction IS NOT NULL
    AND note_rate_fraction > 0.01
    AND note_rate_fraction < 0.15
),
book_band AS (
  -- Exactly one row, even for an empty book (COUNT(*) over no rows is 0 and
  -- the percentiles / thresholds are NULL -- the DDL declares them nullable
  -- for precisely that case).
  SELECT
    COUNT(*)                                                             AS book_lien_count,
    PERCENTILE_APPROX(note_rate_fraction, ARRAY(0.25, 0.5, 0.75), 10000) AS band,
    MAX(min_spread_bps_applied)                                          AS min_spread_bps_applied,
    MAX(min_equity_pct_applied)                                          AS min_equity_pct_applied
  FROM book
),
book_cells AS (
  -- Lossless collapse for the ITM rule: fn_rate_spread reads the note rate
  -- and the market rate; fn_in_the_money reads the spread, equity_pct and
  -- the two thresholds. Nothing else about a borrower changes the verdict.
  SELECT
    note_rate_fraction,
    equity_pct,
    min_spread_bps_applied,
    min_equity_pct_applied,
    COUNT(*) AS lien_count
  FROM book
  GROUP BY note_rate_fraction, equity_pct, min_spread_bps_applied, min_equity_pct_applied
),
weeks AS (
  -- Every observed week, NOT just is_latest: the chart is the history.
  SELECT
    series_id,
    observation_week,
    rate_pct,
    rate_fraction,
    is_latest
  FROM mip.silver.market_rates_weekly
  WHERE series_id = 'MORTGAGE30US'
),
itm_by_week AS (
  SELECT
    w.observation_week,
    SUM(
      CASE
        WHEN mip.gold.fn_in_the_money(
               mip.gold.fn_rate_spread(k.note_rate_fraction, w.rate_fraction),
               k.equity_pct,
               k.min_spread_bps_applied,
               k.min_equity_pct_applied
             )
        THEN k.lien_count
        ELSE 0
      END
    ) AS itm_count
  FROM weeks AS w
  CROSS JOIN book_cells AS k
  GROUP BY w.observation_week
)
SELECT
  w.series_id,
  w.observation_week,
  CAST(w.rate_pct AS DOUBLE)                                   AS market_rate_pct,
  CAST(w.rate_fraction AS DOUBLE)                              AS market_rate_fraction,
  w.is_latest,
  -- element_at is 1-based: [1] = p25, [2] = median, [3] = p75. Fractions
  -- become percent for display parity with borrower_360.current_rate.
  CAST(element_at(bb.band, 2) * 100 AS DOUBLE)                 AS book_median_rate_pct,
  CAST(element_at(bb.band, 1) * 100 AS DOUBLE)                 AS book_p25_rate_pct,
  CAST(element_at(bb.band, 3) * 100 AS DOUBLE)                 AS book_p75_rate_pct,
  CAST(bb.book_lien_count AS BIGINT)                           AS book_lien_count,
  -- An empty book has no cells, so the LEFT JOIN misses: 0, not NULL.
  CAST(COALESCE(i.itm_count, 0) AS BIGINT)                     AS itm_count,
  CAST(bb.min_spread_bps_applied AS INT)                       AS min_spread_bps_applied,
  CAST(bb.min_equity_pct_applied AS INT)                       AS min_equity_pct_applied,
  (SELECT refresh_at FROM refresh_anchor)                      AS book_as_of,
  (SELECT refresh_at FROM refresh_anchor)                      AS refreshed_at
FROM weeks AS w
CROSS JOIN book_band AS bb
LEFT JOIN itm_by_week AS i
  ON i.observation_week = w.observation_week;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh. COMMENT ON
-- COLUMN keeps the Genie grounding / asset-page comments refresh-stable;
-- the SQL file task executes the statements in order.
COMMENT ON COLUMN mip.gold.rate_window_weekly.series_id IS 'FRED series code carried from silver.market_rates_weekly; MORTGAGE30US for Module 0.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.observation_week IS 'Week-starting Monday carried from silver.market_rates_weekly.observation_week. PK.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.market_rate_pct IS 'FRED 30-year fixed rate for the week in percent (6.30 == 6.30%).';
COMMENT ON COLUMN mip.gold.rate_window_weekly.market_rate_fraction IS 'market_rate_pct / 100.0; the fractional form fn_rate_spread consumed for itm_count.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.is_latest IS 'TRUE on the most recent week per series, carried from silver.market_rates_weekly.is_latest; this is the current market print.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.book_median_rate_pct IS 'Median note rate of the current fixed-rate book in percent (percentile_approx, accuracy 10000). NULL only when the book is empty.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.book_p25_rate_pct IS '25th-percentile note rate of the current fixed-rate book in percent (percentile_approx, accuracy 10000). NULL only when the book is empty.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.book_p75_rate_pct IS '75th-percentile note rate of the current fixed-rate book in percent (percentile_approx, accuracy 10000). NULL only when the book is empty.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.book_lien_count IS 'Active fixed-rate first liens in the current book: gold.borrower_360 rows with current_rate > 0 whose silver.lien_current.first_pos_rate_type is FIX and whose bounded rate is strictly inside the 1%..15% clamp. The same value on every week.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.itm_count IS 'Liens in the money at THIS week market rate: fn_in_the_money(fn_rate_spread(note_rate, market_rate_fraction), equity_pct, min_spread_bps_applied, min_equity_pct_applied) summed over the current fixed-rate book. Same primitives and thresholds as gold.lead_scores.in_the_money.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.min_spread_bps_applied IS 'Rate-spread threshold (bps) applied to every week, carried from gold.borrower_360.min_spread_bps_applied. NULL only when the book is empty.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.min_equity_pct_applied IS 'Equity threshold (pct) applied to every week, carried from gold.borrower_360.min_equity_pct_applied. NULL only when the book is empty.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.book_as_of IS 'Refresh anchor of the book distribution: every week is the CURRENT book measured against that week historical rate, not the book as it stood that week.';
COMMENT ON COLUMN mip.gold.rate_window_weekly.refreshed_at IS 'Deterministic refresh anchor from mip.ref.refresh_run_state.';
