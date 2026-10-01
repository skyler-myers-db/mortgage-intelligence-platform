-- =============================================================================
-- gold_segment_combination_rollup.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.segment_combination_rollup` via CTAS: the
--            signal stack (2026-09-21 UI/UX audit, wow-stage-5). One row per
--            non-empty exact set of the six core segment codes a borrower
--            carries, with the addressable count.
--
-- Grain:     One row per combination_key (at most 63 rows).
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent full rebuild.
--            Runs after assert_borrower_360_fresh in mip_refresh_scores and
--            parallelizes with the sibling rollups.
--
-- Key:       REUSED, never forked: the combination_key expression below is
--            COMBINATION_KEY_SQL from
--            backend/services/repositories/databricks_segment_combinations.py,
--            embedded verbatim (the SQL contract test pins it), so the live
--            contactable aggregate the endpoint joins keys rows the same way.
--            Only the six core codes count; the S1.3 overlay codes are
--            ignored, so a borrower lands in exactly one row and every
--            inclusive count is an exact sum of rows. A borrower with no core
--            code has an empty key and no row.
--
-- Contactable counts are deliberately absent: the eligibility predicate reads
-- CURRENT_TIMESTAMP and has one owner (backend/services/eligibility.py), so
-- the endpoint joins a live eligible aggregate onto these rows per request.
--
-- CTAS re-declares clustering/comments/properties because COR TABLE drops
-- DDL metadata on every refresh (2026-06-11 audit P2-8). Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_segment_combination_rollup.sql.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.segment_combination_rollup
CLUSTER BY (combination_key)
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
keyed AS (
  SELECT
    array_join(array_sort(array_distinct(filter(segment_codes, c -> c IN ('itm', 'listed', 'permit', 'investor', 'equity', 'retention')))), '+') AS combination_key
  FROM mip.gold.borrower_360
)
SELECT
  k.combination_key                                                  AS combination_key,
  split(k.combination_key, '[+]')                                    AS segment_codes,
  CAST(size(split(k.combination_key, '[+]')) AS INT)                 AS signal_count,
  CAST(COUNT(*) AS BIGINT)                                           AS addressable_borrowers,
  (SELECT refresh_at FROM refresh_anchor)                            AS refreshed_at
FROM keyed AS k
WHERE k.combination_key IS NOT NULL
  AND k.combination_key <> ''
GROUP BY k.combination_key;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh. COMMENT ON
-- COLUMN keeps the asset-page comments refresh-stable; the SQL file task
-- executes the statements in order.
COMMENT ON TABLE mip.gold.segment_combination_rollup IS 'Signal stack: borrowers per exact set of the six core segment codes (at most 63 rows), from gold.borrower_360 segment membership. Contactable is joined live by the endpoint, never stored. Built by mip_refresh_scores via gold_segment_combination_rollup.sql; read by /api/v1/segments/combinations.';
COMMENT ON COLUMN mip.gold.segment_combination_rollup.combination_key IS 'The distinct core segment codes (itm, listed, permit, investor, equity, retention) a borrower carries, sorted and joined with +. PK.';
COMMENT ON COLUMN mip.gold.segment_combination_rollup.segment_codes IS 'combination_key split on +: the exact set of core segment codes, sorted.';
COMMENT ON COLUMN mip.gold.segment_combination_rollup.signal_count IS 'Number of core codes in the set (1 .. 6).';
COMMENT ON COLUMN mip.gold.segment_combination_rollup.addressable_borrowers IS 'gold.borrower_360 rows carrying exactly these core codes (overlay codes ignored).';
COMMENT ON COLUMN mip.gold.segment_combination_rollup.refreshed_at IS 'Deterministic refresh anchor from mip.ref.refresh_run_state.';
