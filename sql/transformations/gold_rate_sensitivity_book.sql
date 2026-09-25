-- =============================================================================
-- gold_rate_sensitivity_book.sql  (transformation)
-- -----------------------------------------------------------------------------
-- Purpose:   Populate `mip.gold.rate_sensitivity_book` via CTAS: the gated
--            note rate of every gold.borrower_360 borrower a Rate Lever
--            scenario can move. It is a support table for the Rate Lever's
--            live contactable aggregate (/api/v1/geo/rate-sensitivity), so the
--            running App reads gold only: the note gate needs
--            silver.lien_current, and mip.silver is ETL-only
--            (docs/security/GRANTS.md section 5). This CTAS runs as the ETL
--            identity inside mip_refresh_scores.
--
-- Grain:     One row per gold.borrower_360 CLIP whose gated note rate is
--            non-NULL. PK (clip). A CLIP without a row is a gated borrower:
--            the live statement LEFT JOINs this table, so a missing row reads
--            as the same NULL note the rollup keeps, and fn_rate_spread scores
--            it as the no-signal 0 at every step.
--
-- Gate:      REUSED, never forked: note_rate_fraction is NOTE_RATE_GATE_SQL
--            (backend/services/rate_scenario.py), embedded verbatim over
--            gold.borrower_360 AS b LEFT JOIN silver.lien_current AS lc, the
--            same text and join gold_rate_sensitivity_rollup.sql embeds, so
--            the live contactable subset and its precomputed superset read
--            one note per borrower (tests/unit/
--            test_rate_sensitivity_sql_contract.py pins both).
--
-- Pattern:   CREATE OR REPLACE TABLE ... AS SELECT. Idempotent full rebuild.
--            Runs after assert_borrower_360_fresh in mip_refresh_scores and
--            parallelizes with ctas_rate_sensitivity_rollup and the sibling
--            rollups. book_as_of / refreshed_at come from the shared refresh
--            anchor (mip.ref.refresh_run_state), never a per-task clock.
--
-- CTAS re-declares clustering/comments/properties because COR TABLE drops
-- DDL metadata on every refresh (2026-06-11 audit P2-8). Clustering, column
-- COMMENTs, and TBLPROPERTIES mirror sql/ddl/gold_rate_sensitivity_book.sql.
-- =============================================================================

CREATE OR REPLACE TABLE mip.gold.rate_sensitivity_book
CLUSTER BY (clip)
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
gated AS (
  -- The rollup's own book join and note gate: a paid-down lien
  -- (current_rate = 0), a rate AT the 1% / 15% clamp, or a missing lien row
  -- yields NULL (the gate's CASE has no ELSE).
  SELECT
    b.clip,
    CASE WHEN b.current_rate > 0
          AND mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) > 0.01
          AND mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) < 0.15
         THEN mip.gold.fn_bounded_mortgage_rate(lc.first_pos_rate) END AS note_rate_fraction
  FROM mip.gold.borrower_360 AS b
  LEFT JOIN mip.silver.lien_current AS lc
    ON lc.clip = b.clip
)
SELECT
  g.clip,
  g.note_rate_fraction,
  (SELECT refresh_at FROM refresh_anchor) AS book_as_of,
  (SELECT refresh_at FROM refresh_anchor) AS refreshed_at
FROM gated AS g
WHERE g.note_rate_fraction IS NOT NULL;

-- Column comments re-applied post-CTAS (2026-06-11 audit P2-8 follow-up):
-- CREATE OR REPLACE drops DDL column comments on every refresh. COMMENT ON
-- COLUMN keeps the asset-page comments refresh-stable; the SQL file task
-- executes the statements in order.
COMMENT ON COLUMN mip.gold.rate_sensitivity_book.clip IS 'Cotality CLIP of a gold.borrower_360 borrower whose gated note rate is non-NULL. PK.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_book.note_rate_fraction IS 'Gated note rate as a fraction: fn_bounded_mortgage_rate(silver.lien_current.first_pos_rate) when borrower_360.current_rate > 0 and the bounded rate is strictly inside 1%..15% (NOTE_RATE_GATE_SQL). Never NULL here: a gated borrower has no row.';
COMMENT ON COLUMN mip.gold.rate_sensitivity_book.book_as_of IS 'Refresh anchor of the book the notes were read from (mip.ref.refresh_run_state).';
COMMENT ON COLUMN mip.gold.rate_sensitivity_book.refreshed_at IS 'Deterministic refresh anchor from mip.ref.refresh_run_state.';
