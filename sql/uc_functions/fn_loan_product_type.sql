-- =============================================================================
-- fn_loan_product_type
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 loan product-type classification. Collapses
--            the Cotality first-position loan type code plus the original
--            loan amount into the governed product-type vocabulary that
--            drives the PRODUCT TYPE segment filter, SegmentCard facets, and
--            product-type evidence rows.
--
-- Domain:    Cotality ships `first_position_mortgage_loan_type_code`
--            (silver.lien_current.first_pos_loan_type) as CNV / FHA / VA /
--            other coded values (2026-08-08 audit: the live share uses CNV —
--            2,585,874 rows — while this function tested only the legacy
--            CONV spelling, so every conventional loan bucketed 'other' and
--            missed the owner-occupied fit branch; both spellings map). "Jumbo" is not a source code -- it is a
--            derived classification: a conventional first lien whose
--            ORIGINAL amount exceeds the governed conforming loan limit.
--
-- Vocabulary (lowercase, controlled):
--            conventional | jumbo | fha | va | other | NULL
--
-- Semantics:
--            - NULL / blank loan_type_code -> NULL. An unknown source code
--              must read as "unknown", never as a guessed product. This is
--              the same fail-closed posture as fn_in_the_money's NULL rule.
--            - 'CNV'/'CONV' with original_loan_amount > conforming_limit_usd
--              -> 'jumbo' (strictly greater; a loan exactly at the limit is
--              conforming by FHFA definition).
--            - 'CNV'/'CONV' otherwise (including NULL amount / NULL limit, where
--              the jumbo test cannot be evaluated) -> 'conventional'.
--            - 'FHA' -> 'fha'; 'VA' -> 'va'.
--            - Any other non-blank source code -> 'other' (the code is real
--              Cotality signal, just outside the four marketed buckets).
--
-- Threshold: conforming_limit_usd is an explicit input, NOT baked as a UDF
--            default -- same convention as fn_in_the_money. The governed
--            value lives in mip.ref.offer_rules_config under
--            `mip_conforming_loan_limit_usd` (seeded 806500 = FHFA 2025
--            baseline one-unit conforming loan limit) so operators retune
--            it annually without a SQL deploy.
--
-- NULLs:     NULL code -> NULL. NULL amount or NULL limit only disables the
--            jumbo branch; the code-mapped bucket still returns.
--
-- Determinism: Pure string/number arithmetic. Safe for gold
--            materializations and metric views.
--
-- Parity:    backend/services/scoring.py::loan_product_type mirrors this
--            function 1:1, pinned by tests/fixtures/loan_product_type_golden.json.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_loan_product_type(
  loan_type_code       STRING,
  original_loan_amount BIGINT,
  conforming_limit_usd BIGINT
)
RETURNS STRING
DETERMINISTIC
COMMENT 'Module 0 canonical loan product-type classification: conventional / jumbo / fha / va / other / NULL from the Cotality first-position loan type code plus original amount vs. the governed conforming loan limit (mip.ref.offer_rules_config key mip_conforming_loan_limit_usd). NULL code returns NULL (unknown never guesses). See tests/fixtures/loan_product_type_golden.json for parity fixtures.'
RETURN
  CASE
    WHEN loan_type_code IS NULL OR LENGTH(TRIM(loan_type_code)) = 0 THEN NULL
    WHEN UPPER(TRIM(loan_type_code)) IN ('CNV', 'CONV')
     AND original_loan_amount IS NOT NULL
     AND conforming_limit_usd IS NOT NULL
     AND original_loan_amount > conforming_limit_usd
    THEN 'jumbo'
    WHEN UPPER(TRIM(loan_type_code)) IN ('CNV', 'CONV') THEN 'conventional'
    WHEN UPPER(TRIM(loan_type_code)) = 'FHA'  THEN 'fha'
    WHEN UPPER(TRIM(loan_type_code)) = 'VA'   THEN 'va'
    ELSE 'other'
  END;
