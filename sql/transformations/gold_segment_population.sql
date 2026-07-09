-- =============================================================================
-- gold_segment_population.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.segment_population` via CTAS. One row
--            per (segment_code, state) + one row per (segment_code, '_ALL')
--            national rollup. Also APPENDS today's snapshot to
--            gold.segment_population_prior for the next refresh's delta.
--
-- Grain:     (segment_code, state). One row for each segment in each refreshed
--            coverage state plus one _ALL row per segment.
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT for the current table;
--            MERGE on (segment_code, state, snapshot_date) for the prior
--            snapshot append (idempotent if re-run same day).
-- Slice:     module0-real-data-slice3.
-- Data contract: docs/data-contract-module0.md §3.4 + §3.6.
--
-- Segment membership is computed from gold.borrower_360.segment_codes.
-- The `listed` segment is live from Cotality MLS rows. The legacy `permit`
-- segment code is retained for API compatibility but is displayed as HELOC
-- Intent and is currently driven by Cotality HELOC propensity; the filed
-- Building Permits feed remains pending until a true permit source table lands.
--
-- delta_vs_prior:
--   * Pull prior count from gold.segment_population_prior for snapshot_date =
--     current_date - 1 (or the most recent available if -1 is missing).
--   * Format as '+NN%' / '-NN%', safe-divide when prior=0 ("+0%").
--   * On first refresh (no prior), delta_vs_prior = '+0%'.
--
-- Segment metadata (name / description / color) is taken from the static
-- map inline -- matches mock_data.SEGMENTS for screen parity.
-- =============================================================================

-- 1) Append today's snapshot to the prior-period table, idempotent on
--    (segment_code, state, snapshot_date). Runs BEFORE the main CTAS so the
--    main CTAS's delta calculation reads yesterday's row, not today's.
MERGE INTO mip.gold.segment_population_prior AS t
USING (
  WITH exploded AS (
    SELECT
      b.state,
      sc AS segment_code,
      b.opportunity_score
    FROM mip.gold.borrower_360 AS b
    LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
  ),
  per_state AS (
    SELECT
      segment_code,
      state,
      CURRENT_DATE()                                AS snapshot_date,
      CAST(COUNT(*) AS INT)                         AS cnt,
      CAST(ROUND(AVG(opportunity_score)) AS INT)    AS avg_score
    FROM exploded
    GROUP BY segment_code, state
  ),
  national AS (
    SELECT
      segment_code,
      '_ALL'                                        AS state,
      CURRENT_DATE()                                AS snapshot_date,
      CAST(COUNT(*) AS INT)                         AS cnt,
      CAST(ROUND(AVG(opportunity_score)) AS INT)    AS avg_score
    FROM exploded
    GROUP BY segment_code
  )
  SELECT segment_code, state, snapshot_date, cnt AS count, avg_score FROM per_state
  UNION ALL
  SELECT segment_code, state, snapshot_date, cnt AS count, avg_score FROM national
) AS s
  ON t.segment_code = s.segment_code
  AND t.state       = s.state
  AND t.snapshot_date = s.snapshot_date
WHEN MATCHED THEN UPDATE SET
  count     = s.count,
  avg_score = s.avg_score
WHEN NOT MATCHED THEN INSERT (
  segment_code, state, snapshot_date, count, avg_score
) VALUES (
  s.segment_code, s.state, s.snapshot_date, s.count, s.avg_score
);

-- 2) Rebuild segment_population with the current-day counts + derived delta.
--
-- 2026-05-04 fix (prototype-parity-audit P0-2): the prior implementation
-- aggregated FROM gold.borrower_360.segment_codes via LATERAL VIEW EXPLODE,
-- which means a segment with zero matching borrowers produced ZERO ROWS in
-- the rollup. At the time, `listed` and `permit` were both blocked-FALSE in
-- gold.borrower_360 pending source arrival, so /api/segments was returning
-- only 4 of the contracted 6 segments and the frontend was rendering "4
-- borrower segments" instead of the prototype's 6. MLS/listing activity now
-- flows through `mip.silver.listing_activity`; filed Building Permits remain
-- the pending predicate. The fix is to drive the rollup off the `meta` VALUES
-- table (the canonical 6-segment registry) and LEFT JOIN exploded counts onto
-- it -- segments with no matching borrowers now appear as count=0 / avg_score=0
-- and the FE can render any genuinely unavailable source in a pending state
-- instead of disappearing entirely.
-- Honest UX: zero counts mean zero counts, but the segment is still
-- visible so the demo narrative ("you'll see 6 segments") holds.
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_segment_population.sql; the
-- column list order matches the final SELECT projection 1:1. (The prior-period
-- MERGE above writes segment_population_prior, whose metadata MERGE preserves.)
CREATE OR REPLACE TABLE mip.gold.segment_population
CLUSTER BY (segment_code)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH exploded AS (
  SELECT
    b.state,
    sc AS segment_code,
    b.opportunity_score
  FROM mip.gold.borrower_360 AS b
  LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
),
per_state AS (
  SELECT
    segment_code,
    state,
    CAST(COUNT(*) AS INT)                      AS count,
    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_score
  FROM exploded
  GROUP BY segment_code, state
),
national AS (
  SELECT
    segment_code,
    '_ALL'                                     AS state,
    CAST(COUNT(*) AS INT)                      AS count,
    CAST(ROUND(AVG(opportunity_score)) AS INT) AS avg_score
  FROM exploded
  GROUP BY segment_code
),
current_counts AS (
  SELECT * FROM per_state
  UNION ALL
  SELECT * FROM national
),
-- Most recent prior snapshot strictly before today. Takes the MAX snapshot
-- date < today for each (segment_code, state); first-refresh rows have no
-- prior and emit prior_count = 0 -> delta '+0%'.
prior AS (
  SELECT
    segment_code, state,
    count                 AS prior_count
  FROM (
    SELECT
      segment_code, state, count,
      ROW_NUMBER() OVER (PARTITION BY segment_code, state
                         ORDER BY snapshot_date DESC)          AS rn
    FROM mip.gold.segment_population_prior
    WHERE snapshot_date < CURRENT_DATE()
  ) q
  WHERE rn = 1
),
-- Segment metadata inline so gold is self-contained. This is the canonical
-- segment registry (6 core + 7 S1.3 overlays) that the rollup
-- CROSS-JOIN-spans below; every refresh
-- emits a row per (segment_code, state) including states + the _ALL
-- aggregate, so the API can never silently drop a segment whose predicate
-- matched zero borrowers.
--
-- 2026-06-13 update: MLS/listing is live. The legacy permit segment code now
-- describes HELOC Intent from Cotality HELOC propensity while filed Building
-- Permits remain a separate pending source-readiness row.
meta AS (
  SELECT * FROM (
    VALUES
      ('itm',       'Prime Refi Candidates',    'Lien rate >= 75 bps above par and equity >= 15%.',                                      '#5CE1E6'),
      ('listed',    'Listed for Sale',          'Current active or under-contract Cotality MLS listing tied to CLIP.', '#F59E0B'),
      ('permit',    'HELOC Intent',             'Cotality HELOC propensity >= 700 with equity context. Filed Building Permits remain pending until a true permit source lands.', '#A78BFA'),
      ('investor',  'Investor / Multi-Property','Owner Link shows 2+ properties or repeat behavior.',                                    '#F472B6'),
      ('equity',    'Home Equity Candidate',    'Strong equity and no active second-position balance.',                                  '#66C5FF'),
      ('retention', 'Retention Risk',           'Current-customer or recapture signals worth reviewing before the borrower shops alternatives.', '#34D399'),
      -- S1.3 overlay segments. Membership predicates live in
      -- gold_borrower_360.sql (with_segments) and each column comment; the
      -- refi_propensity heuristic is published verbatim in
      -- fn_refi_propensity_heuristic.sql + the app glossary.
      ('second_lien_itm',         'Second-Lien Consolidation', 'Open second position whose rate clears the same governed spread/equity thresholds as first-lien ITM.', '#E879F9'),
      ('heloc_draw_to_payback',   'HELOC Draw Ending',         'Open equity-loan lien whose standard 120-month draw period ends within 18 months or ended within the last 6.', '#FB923C'),
      ('home_equity_history',     'Home Equity History',       'Appreciation >= 40% since purchase, owned >= 36 months, current equity >= 20%.', '#A3E635'),
      ('refi_propensity',         'Refi Propensity',           'Transparent deterministic heuristic >= 60 of 100. Published points table over spread, seasoning, equity, balance, and listing status.', '#818CF8'),
      ('itm_on_related_property', 'ITM on Related Property',   'An Owner Link on this property also holds a different property that is in the money.', '#38BDF8'),
      ('payoff_loss_leads',       'Payoff Loss',               'Tenant lien released within 24 months and the property now carries a competitor lien.', '#F87171'),
      ('permit_activity',         'Permit Activity',           'Filed building-permit activity. Pending until a true Cotality permit source table lands; never inferred from propensity models.', '#C4B5FD')
  ) AS t(segment_code, name, description, color)
),
-- Build the full (segment_code, state) grid up front so segments with zero
-- matching borrowers still get a row. We span all states present in the
-- exploded rollup PLUS the canonical _ALL national row -- if a workspace
-- has no borrowers in a particular state at all, no _per-state_ row is
-- emitted for that state (consistent with the prior behavior); but every
-- segment_code always appears in the _ALL row, which is what the FE reads.
states_seen AS (
  SELECT DISTINCT state FROM current_counts
  UNION
  SELECT '_ALL' AS state
),
grid AS (
  SELECT m.segment_code, s.state
  FROM meta AS m
  CROSS JOIN states_seen AS s
)
SELECT
  g.segment_code,
  g.state,
  m.name,
  COALESCE(c.count, 0)                              AS count,
  -- '+NN%' / '-NN%' / '+0%'. Safe-divide: prior=0 or NULL -> '+0%'.
  -- Segments with zero current and zero prior naturally collapse to '+0%'.
  CASE
    WHEN COALESCE(p.prior_count, 0) = 0 THEN '+0%'
    ELSE
      CONCAT(
        CASE WHEN COALESCE(c.count, 0) >= p.prior_count THEN '+' ELSE '' END,
        CAST(CAST(ROUND(100.0 * (COALESCE(c.count, 0) - p.prior_count) / p.prior_count) AS INT) AS STRING),
        '%'
      )
  END                                                AS delta_vs_prior,
  COALESCE(c.avg_score, 0)                          AS avg_score,
  m.description,
  m.color,
  -- Shared refresh_at captured once per run. See audit-holes-round-3 #7.
  (SELECT refresh_at FROM mip.ref.refresh_run_state ORDER BY captured_at DESC LIMIT 1) AS refreshed_at
FROM grid           AS g
LEFT JOIN meta      AS m USING (segment_code)
LEFT JOIN current_counts AS c USING (segment_code, state)
LEFT JOIN prior     AS p USING (segment_code, state);

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.segment_population.segment_code IS 'itm / listed / permit / investor / equity / retention + S1.3 overlays second_lien_itm / heloc_draw_to_payback / home_equity_history / refi_propensity / itm_on_related_property / payoff_loss_leads / permit_activity. Matches SegmentCode Literal exactly; permit is the backward-compatible code for customer-facing HELOC Intent.';
COMMENT ON COLUMN mip.gold.segment_population.state IS '2-char state code from refreshed source coverage or "_ALL" for national rollup.';
COMMENT ON COLUMN mip.gold.segment_population.name IS 'Static label per segment_code (e.g., "Prime Refi Candidates").';
COMMENT ON COLUMN mip.gold.segment_population.count IS 'Member count for this (segment, state) cell.';
COMMENT ON COLUMN mip.gold.segment_population.delta_vs_prior IS 'Quarter-over-quarter delta as "+NN%" / "-NN%". Router maps to SegmentSummary.delta. "+0%" on first refresh.';
COMMENT ON COLUMN mip.gold.segment_population.avg_score IS 'CAST(ROUND(AVG(opportunity_score)) AS INT) over the segment cell.';
COMMENT ON COLUMN mip.gold.segment_population.description IS 'Static description per segment_code.';
COMMENT ON COLUMN mip.gold.segment_population.color IS 'Hex color for segment tile.';
COMMENT ON COLUMN mip.gold.segment_population.refreshed_at IS 'Refresh timestamp.';
