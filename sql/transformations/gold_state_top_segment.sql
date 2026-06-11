-- =============================================================================
-- gold_state_top_segment.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.state_top_segment` via CTAS. One row per
--            (state, snapshot_date) -- the dominant SegmentCode per state +
--            the share % of that state's borrowers in the top segment.
--            Feeds the `top_segment_code` extension on /api/geo/state-rollups.
--
-- Grain:     (state, snapshot_date).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT.
-- Slice:     slice13-accuracy-validation.
--
-- Source:    mip.gold.borrower_360 -- segment_codes is ARRAY<STRING>; we
--            LATERAL VIEW EXPLODE, then GROUP BY (state, segment_code) and
--            rank DESC to pick the top segment per state.
--
-- Edge case: a state with every borrower carrying an empty segment_codes
--            array still emits one row with top_segment_code = 'none' +
--            top_segment_share_pct = 0 so the frontend sees a predictable
--            shape rather than a missing row.
--
-- 2026-06-11 audit P2-8: CTAS re-declares clustering/comments/properties
-- because COR TABLE drops DDL metadata on every refresh. Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_state_top_segment.sql.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.state_top_segment
CLUSTER BY (state)
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
)
AS
WITH state_pop AS (
  SELECT
    state,
    CAST(COUNT(*) AS INT) AS addressable
  FROM mip.gold.borrower_360
  GROUP BY state
),
exploded AS (
  SELECT
    b.state,
    sc AS segment_code
  FROM mip.gold.borrower_360 AS b
  LATERAL VIEW EXPLODE(b.segment_codes) s AS sc
  WHERE sc IS NOT NULL
),
segment_counts AS (
  SELECT
    state,
    segment_code,
    COUNT(*) AS segment_count,
    ROW_NUMBER() OVER (
      PARTITION BY state
      ORDER BY COUNT(*) DESC, segment_code ASC
    ) AS rn
  FROM exploded
  GROUP BY state, segment_code
),
top_segment AS (
  SELECT
    state,
    segment_code AS top_segment_code,
    segment_count
  FROM segment_counts
  WHERE rn = 1
)
SELECT
  sp.state,
  COALESCE(ts.top_segment_code, 'none')                                         AS top_segment_code,
  CAST(
    CASE
      WHEN sp.addressable > 0 AND ts.segment_count IS NOT NULL
        THEN GREATEST(0, LEAST(100, ROUND(100.0 * ts.segment_count / sp.addressable)))
      ELSE 0
    END
  AS INT)                                                                       AS top_segment_share_pct,
  CURRENT_DATE()                                                                AS snapshot_date
FROM state_pop AS sp
LEFT JOIN top_segment AS ts ON ts.state = sp.state;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh, and the
-- typeless CTAS column list is a PARSE_SYNTAX_ERROR on DBSQL (observed
-- live, run 2026-06-11). COMMENT ON COLUMN keeps the Genie grounding /
-- asset-page comments refresh-stable; the SQL file task executes the
-- statements in order.
COMMENT ON COLUMN mip.gold.state_top_segment.state IS '2-char USPS state code (uppercase). PK part.';
COMMENT ON COLUMN mip.gold.state_top_segment.top_segment_code IS 'Dominant SegmentCode for this state on snapshot_date (itm/listed/permit/investor/equity/retention). "none" when no borrower in the state has a non-empty segment_codes array.';
COMMENT ON COLUMN mip.gold.state_top_segment.top_segment_share_pct IS '0..100. Share of the state population in the top segment.';
COMMENT ON COLUMN mip.gold.state_top_segment.snapshot_date IS 'Refresh date; daily grain. PK part.';
