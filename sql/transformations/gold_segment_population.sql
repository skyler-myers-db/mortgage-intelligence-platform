-- =============================================================================
-- gold_segment_population.sql (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip_demo.gold.segment_population` via CTAS. One row
--            per (segment_code, state) + one row per (segment_code, '_ALL')
--            national rollup. Also APPENDS today's snapshot to
--            gold.segment_population_prior for the next refresh's delta.
--
-- Grain:     (segment_code, state). 6 segments * (6 states + _ALL) = 42 rows.
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
MERGE INTO mip_demo.gold.segment_population_prior AS t
USING (
  WITH exploded AS (
    SELECT
      b.state,
      sc AS segment_code,
      b.opportunity_score
    FROM mip_demo.gold.borrower_360 AS b
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
CREATE OR REPLACE TABLE mip_demo.gold.segment_population AS
WITH exploded AS (
  SELECT
    b.state,
    sc AS segment_code,
    b.opportunity_score
  FROM mip_demo.gold.borrower_360 AS b
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
    FROM mip_demo.gold.segment_population_prior
    WHERE snapshot_date < CURRENT_DATE()
  ) q
  WHERE rn = 1
),
-- Segment metadata inline so gold is self-contained.
meta AS (
  SELECT * FROM (
    VALUES
      ('itm',       'In the Money',             'Lien rate >= 75 bps above par and equity >= 15%.',                                      '#5CE1E6'),
      ('listed',    'Listed for Sale',          'Active listing, likely purchase mortgage opportunity.',                                 '#F59E0B'),
      ('permit',    'Permit Activity',          'Recent high-value permits indicate HELOC/cash-out demand.',                             '#A78BFA'),
      ('investor',  'Investor / Multi-Property','Owner Link shows 2+ properties or repeat behavior.',                                    '#F472B6'),
      ('equity',    'Home Equity Candidate',    'Strong equity and prior cash-out/HELOC propensity.',                                    '#66C5FF'),
      ('retention', 'Retention Risk',           'Current customer showing refi/listing/competitor signals.',                             '#34D399')
  ) AS t(segment_code, name, description, color)
)
SELECT
  c.segment_code,
  c.state,
  m.name,
  c.count,
  -- '+NN%' / '-NN%' / '+0%'. Safe-divide: prior=0 or NULL -> '+0%'.
  CASE
    WHEN COALESCE(p.prior_count, 0) = 0 THEN '+0%'
    ELSE
      CONCAT(
        CASE WHEN c.count >= p.prior_count THEN '+' ELSE '' END,
        CAST(CAST(ROUND(100.0 * (c.count - p.prior_count) / p.prior_count) AS INT) AS STRING),
        '%'
      )
  END                                                AS delta_vs_prior,
  c.avg_score,
  m.description,
  m.color,
  CURRENT_TIMESTAMP()                                AS refreshed_at
FROM current_counts AS c
LEFT JOIN prior    AS p USING (segment_code, state)
LEFT JOIN meta     AS m USING (segment_code);
