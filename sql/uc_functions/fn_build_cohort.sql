-- =============================================================================
-- fn_build_cohort
-- -----------------------------------------------------------------------------
-- Purpose:   Reviewed UC-function tool contract for broad Growth Agent
--            borrower cohorts. Returns a de-duplicated borrower count from
--            gold.borrower_360 for reviewed segment filters and optional state
--            scope. This is read-only and has no activation side effects.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_build_cohort(
  segment_codes ARRAY<STRING>,
  segment_mode  STRING,
  states        ARRAY<STRING>
)
RETURNS BIGINT
DETERMINISTIC
COMMENT 'Reviewed Mortgage Growth Agent broad cohort tool. Counts DISTINCT clip from gold.borrower_360 using any/all segment semantics and optional state scope. Read-only.'
RETURN (
  SELECT COUNT(DISTINCT b.clip)
  FROM mip.gold.borrower_360 AS b
  WHERE (
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
