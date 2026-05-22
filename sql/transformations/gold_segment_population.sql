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
-- Segment membership is computed from gold.borrower_360.segment_codes
-- (already derived there with the listed/permit BLOCKED columns forced
-- false). Using the pre-computed array keeps this transformation a pure
-- aggregate rather than re-deriving predicates.
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
-- the rollup. The `listed` and `permit` predicates are blocked-FALSE in
-- gold.borrower_360 pending the Cotality MLS + Building-Permits Delta Share
-- arrival, so /api/segments was returning only 4 of the contracted 6
-- segments and the frontend was rendering "4 borrower segments" instead of
-- the prototype's 6. The fix is to drive the rollup off the `meta` VALUES
-- table (the canonical 6-segment registry) and LEFT JOIN exploded counts
-- onto it -- segments with no matching borrowers now appear as count=0 /
-- avg_score=0 and the FE can render them in a "Awaiting Cotality MLS /
-- Permits Delta Share" pending state instead of disappearing entirely.
-- Honest UX: zero counts mean zero counts, but the segment is still
-- visible so the demo narrative ("you'll see 6 segments") holds.
CREATE OR REPLACE TABLE mip.gold.segment_population AS
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
-- 6-segment registry that the rollup CROSS-JOIN-spans below; every refresh
-- emits a row per (segment_code, state) including states + the _ALL
-- aggregate, so the API can never silently drop a segment whose predicate
-- matched zero borrowers.
meta AS (
  SELECT * FROM (
    VALUES
      ('itm',       'In the Money',             'Lien rate >= 75 bps above par and equity >= 15%.',                                      '#5CE1E6'),
      ('listed',    'Listed for Sale',          'Pending Cotality MLS share; listed-for-sale predicates are blocked false until the feed lands.', '#F59E0B'),
      ('permit',    'Permit Activity',          'Pending Cotality Building Permits share; permit predicates are blocked false until the feed lands.', '#A78BFA'),
      ('investor',  'Investor / Multi-Property','Owner Link shows 2+ properties or repeat behavior.',                                    '#F472B6'),
      ('equity',    'Home Equity Candidate',    'Strong equity and no active second-position balance.',                                  '#66C5FF'),
      ('retention', 'Retention Risk',           'Current customer with rate spread above the retention threshold; live listing and competitor overlays join only when source evidence is present.', '#34D399')
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
