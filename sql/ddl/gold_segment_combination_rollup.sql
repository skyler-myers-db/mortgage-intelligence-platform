-- =============================================================================
-- gold_segment_combination_rollup.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.segment_combination_rollup` -- the signal
--            stack (2026-09-21 UI/UX audit, wow-stage-5): where several
--            Cotality signals fire on the same borrower. One row per
--            non-empty EXACT set of the six core segment codes a borrower
--            carries.
--
-- Grain:     One row per combination_key (PK): at most 2^6 - 1 = 63 rows.
-- Clustering: Liquid cluster on (combination_key).
--
-- Key:       combination_key = the borrower's distinct core codes (itm,
--            listed, permit, investor, equity, retention), sorted and joined
--            with '+'. The S1.3 overlay codes are ignored, so every inclusive
--            count ("at least these signals") is an exact sum of rows. The
--            fragment has one owner (COMBINATION_KEY_SQL in
--            backend/services/repositories/databricks_segment_combinations.py)
--            and the CTAS carries it verbatim.
--
-- Semantics: addressable only. Contactable counts are NOT a column: the
--            eligibility predicate reads CURRENT_TIMESTAMP and has one owner
--            (backend/services/eligibility.py), so the endpoint joins a live
--            eligible aggregate onto these rows per request.
--
-- Cost:      Built once per gold refresh by mip_refresh_scores
--            (ctas_segment_combination_rollup). Never on a schedule of its own.
--
-- Contract:  pinned by tests/unit/test_segment_combination_sql_contract.py and,
--            against real UC, tests/integration/test_segment_combination_parity.py.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.segment_combination_rollup (
  combination_key        STRING        NOT NULL COMMENT 'The distinct core segment codes (itm, listed, permit, investor, equity, retention) a borrower carries, sorted and joined with +. PK.',
  segment_codes          ARRAY<STRING> NOT NULL COMMENT 'combination_key split on +: the exact set of core segment codes, sorted.',
  signal_count           INT           NOT NULL COMMENT 'Number of core codes in the set (1 .. 6).',
  addressable_borrowers  BIGINT        NOT NULL COMMENT 'gold.borrower_360 rows carrying exactly these core codes (overlay codes ignored).',
  refreshed_at           TIMESTAMP     NOT NULL COMMENT 'Deterministic refresh anchor from mip.ref.refresh_run_state.'
)
USING DELTA
CLUSTER BY (combination_key)
COMMENT 'Signal stack: borrowers per exact set of the six core segment codes (at most 63 rows), from gold.borrower_360 segment membership. Contactable is joined live by the endpoint, never stored. Built by mip_refresh_scores via gold_segment_combination_rollup.sql; read by /api/v1/segments/combinations.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
