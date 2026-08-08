-- =============================================================================
-- loan_product_type_validation.sql
-- -----------------------------------------------------------------------------
-- Purpose:  Cross-platform parity check. Asserts that
--           mip.gold.fn_loan_product_type(...) produces the same buckets as
--           tests/fixtures/loan_product_type_golden.json (which is also the
--           contract for backend/services/scoring.py::loan_product_type).
--
-- How to run:
--   Open a Databricks SQL warehouse session, run
--   sql/uc_functions/fn_loan_product_type.sql first, then this file. The
--   `mismatch` column must be empty on every row.
--
-- Default threshold: conforming_limit_usd=806500 (FHFA 2025 baseline one-unit
--   conforming loan limit, seeded into mip.ref.offer_rules_config under
--   mip_conforming_loan_limit_usd). The limit is passed explicitly per-call;
--   it is not baked into the UDF.
--
-- Owner:   data-modeler. Keep in sync with the JSON fixture on any edit.
-- =============================================================================

WITH golden (id, loan_type_code, original_loan_amount, conforming_limit_usd, expected_product_type) AS (
  VALUES
    ('case_01_conv_below_limit_conventional',     'CONV', 340000, 806500, 'conventional'),
    ('case_02_conv_above_limit_jumbo',            'CONV', 950000, 806500, 'jumbo'),
    ('case_03_conv_exactly_at_limit_conventional','CONV', 806500, 806500, 'conventional'),
    ('case_04_fha_maps_fha',                      'FHA',  900000, 806500, 'fha'),
    ('case_05_va_maps_va',                        'VA',   250000, 806500, 'va'),
    ('case_06_null_code_returns_null',            CAST(NULL AS STRING), 950000, 806500, CAST(NULL AS STRING)),
    ('case_07_blank_code_returns_null',           '   ',  340000, 806500, CAST(NULL AS STRING)),
    ('case_08_unrecognized_code_other',           'PP',   340000, 806500, 'other'),
    ('case_09_case_and_whitespace_normalized',    ' conv ', 950000, 806500, 'jumbo'),
    ('case_10_conv_null_amount_conventional',     'CONV', CAST(NULL AS BIGINT), 806500, 'conventional'),
    ('case_11_conv_null_limit_conventional',      'CONV', 950000, CAST(NULL AS BIGINT), 'conventional'),
    ('case_12_cnv_below_limit_conventional',      'CNV', 340000, 806500, 'conventional'),
    ('case_13_cnv_above_limit_jumbo',             'CNV', 950000, 806500, 'jumbo'),
    ('case_14_cnv_lowercase_trimmed',             ' cnv ', CAST(NULL AS BIGINT), 806500, 'conventional'),
    ('case_15_pp_private_party_other',            'PP', 300000, 806500, 'other')
)
SELECT
  id,
  expected_product_type,
  mip.gold.fn_loan_product_type(loan_type_code, original_loan_amount, conforming_limit_usd) AS actual_product_type,
  CASE
    WHEN mip.gold.fn_loan_product_type(loan_type_code, original_loan_amount, conforming_limit_usd) <=> expected_product_type
    THEN NULL
    ELSE 'MISMATCH'
  END AS mismatch
FROM golden
ORDER BY id;
