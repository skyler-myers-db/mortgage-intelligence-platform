-- =============================================================================
-- gold_rate_sensitivity_book.sql
-- -----------------------------------------------------------------------------
-- Purpose:   DDL for `mip.gold.rate_sensitivity_book` -- the gated note rate of
--            every gold.borrower_360 borrower a Rate Lever scenario can move
--            (2026-09-21 UI/UX audit, wow-stage-1). A support table: the
--            endpoint's live contactable aggregate LEFT JOINs it on CLIP so
--            the running App reads gold only (mip.silver is ETL-only,
--            docs/security/GRANTS.md section 5).
--
-- Grain:     One row per gold.borrower_360 CLIP whose gated note rate is
--            non-NULL. PK (clip). A CLIP without a row is a gated borrower
--            (no active lien, a rate AT the 1% / 15% clamp, no lien row); the
--            LEFT JOIN reads it as the NULL note the rollup keeps.
-- Clustering: Liquid cluster on (clip), the join key.
--
-- Gate:      note_rate_fraction is NOTE_RATE_GATE_SQL
--            (backend/services/rate_scenario.py) over gold.borrower_360 AS b
--            LEFT JOIN silver.lien_current AS lc -- the same text and join as
--            gold_rate_sensitivity_rollup.sql, so the live subset and the
--            precomputed superset read one note per borrower.
--
-- Cost:      Built once per gold refresh by mip_refresh_scores
--            (ctas_rate_sensitivity_book). Never on a schedule of its own.
--
-- Contract:  pinned by tests/unit/test_rate_sensitivity_sql_contract.py and,
--            against real UC, tests/integration/test_rate_sensitivity_parity.py.
-- =============================================================================

CREATE TABLE IF NOT EXISTS mip.gold.rate_sensitivity_book (
  clip                 STRING    NOT NULL COMMENT 'Cotality CLIP of a gold.borrower_360 borrower whose gated note rate is non-NULL. PK.',
  note_rate_fraction   DOUBLE    NOT NULL COMMENT 'Gated note rate as a fraction: fn_bounded_mortgage_rate(silver.lien_current.first_pos_rate) when borrower_360.current_rate > 0 and the bounded rate is strictly inside 1%..15% (NOTE_RATE_GATE_SQL). Never NULL here: a gated borrower has no row.',
  book_as_of           TIMESTAMP NOT NULL COMMENT 'Refresh anchor of the book the notes were read from (mip.ref.refresh_run_state).',
  refreshed_at         TIMESTAMP NOT NULL COMMENT 'Deterministic refresh anchor from mip.ref.refresh_run_state.'
)
USING DELTA
CLUSTER BY (clip)
COMMENT 'Rate Lever support table: the gated note rate (NOTE_RATE_GATE_SQL over gold.borrower_360 LEFT JOIN silver.lien_current) of every borrower a par-rate scenario can move, one row per CLIP. Built by mip_refresh_scores via gold_rate_sensitivity_book.sql as the ETL identity; read by /api/v1/geo/rate-sensitivity so the App never reads mip.silver.'
TBLPROPERTIES (
  'delta.enableChangeDataFeed' = 'false',
  'delta.autoOptimize.optimizeWrite' = 'true',
  'delta.autoOptimize.autoCompact'   = 'true'
);
