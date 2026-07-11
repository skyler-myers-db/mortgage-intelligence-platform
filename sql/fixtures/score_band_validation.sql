-- =============================================================================
-- score_band_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_score_band(...) and mip.gold.fn_high_opportunity(...)
--           produce the same bands/flags as tests/fixtures/
--           score_band_golden.json (which is also the contract for
--           backend/services/scoring.py::score_band / is_high_opportunity
--           and frontend/src/lib/opportunityScore.ts::scoreBand).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_score_band.sql and
--   sql/uc_functions/fn_high_opportunity.sql first, then this file.
--   The `mismatch` column must be empty on every row.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (id, opportunity_score, expected_band, expected_high_opportunity) AS (
  VALUES
    ('case_01_null_score',       CAST(NULL AS INT), 'low',  FALSE),
    ('case_02_zero',             0,                 'low',  FALSE),
    ('case_03_band_edge_64',     64,                'low',  FALSE),
    ('case_04_band_edge_65',     65,                'med',  FALSE),
    ('case_05_band_edge_66',     66,                'med',  FALSE),
    ('case_06_threshold_edge_74', 74,               'med',  FALSE),
    ('case_07_threshold_edge_75', 75,               'med',  TRUE),
    ('case_08_threshold_edge_76', 76,               'med',  TRUE),
    ('case_09_band_edge_84',     84,                'med',  TRUE),
    ('case_10_band_edge_85',     85,                'high', TRUE),
    ('case_11_band_edge_86',     86,                'high', TRUE),
    ('case_12_ceiling_100',      100,               'high', TRUE)
),
evaluated AS (
  SELECT
    id,
    opportunity_score,
    expected_band,
    expected_high_opportunity,
    mip.gold.fn_score_band(opportunity_score)       AS actual_band,
    mip.gold.fn_high_opportunity(opportunity_score) AS actual_high_opportunity
  FROM golden
)
SELECT
  id,
  opportunity_score,
  expected_band,
  actual_band,
  expected_high_opportunity,
  actual_high_opportunity,
  CASE
    WHEN actual_band <> expected_band
      OR actual_high_opportunity <> expected_high_opportunity
    THEN 'MISMATCH'
    ELSE ''
  END AS mismatch
FROM evaluated
ORDER BY id;
