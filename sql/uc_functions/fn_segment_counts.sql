-- =============================================================================
-- fn_segment_counts
-- -----------------------------------------------------------------------------
-- Purpose:   Reviewed UC-function tool contract for actionability gates.
--            Returns a de-duplicated eligible lead count from gold.borrower_360
--            using explicit any/all segment semantics.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_segment_counts(
  segment_codes ARRAY<STRING>,
  segment_mode  STRING,
  states        ARRAY<STRING>
)
RETURNS BIGINT
DETERMINISTIC
COMMENT 'Reviewed Mortgage Growth Agent actionability tool. Counts DISTINCT clip from gold.borrower_360 after marketing eligibility, opt-in, and suppression gates. Read-only.'
RETURN (
  SELECT COUNT(DISTINCT b.clip)
  FROM mip.gold.borrower_360 AS b
  WHERE b.marketing_eligible = TRUE
    AND b.consent_status = 'opt_in'
    AND b.suppression_reason IS NULL
    AND (
      SIZE(COALESCE(segment_codes, ARRAY())) = 0
      OR CASE
        WHEN LOWER(COALESCE(segment_mode, 'any')) = 'all'
          THEN SIZE(ARRAY_EXCEPT(TRANSFORM(segment_codes, x -> LOWER(x)), b.segment_codes)) = 0
        ELSE SIZE(ARRAY_INTERSECT(TRANSFORM(segment_codes, x -> LOWER(x)), b.segment_codes)) > 0
      END
    )
    AND (
      SIZE(COALESCE(states, ARRAY())) = 0
      OR ARRAY_CONTAINS(TRANSFORM(states, x -> UPPER(x)), UPPER(b.state))
    )
);
