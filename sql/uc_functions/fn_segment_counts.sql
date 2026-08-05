-- =============================================================================
-- fn_segment_counts
-- -----------------------------------------------------------------------------
-- Purpose:   Reviewed UC-function tool contract for actionability gates.
--            Returns a de-duplicated eligible lead count from gold.borrower_360
--            using explicit any/all segment semantics.
-- =============================================================================

-- S1.4: the eligibility gate below is the FULL fail-closed contact-
-- eligibility predicate and must stay equivalent (modulo whitespace) with
-- backend/services/eligibility.py::eligible_sql_predicate("b").
-- tests/unit/test_contact_eligibility.py pins the lockstep. The function
-- is explicitly declared NOT DETERMINISTIC because the frequency-cap guard
-- reads CURRENT_TIMESTAMP().
CREATE OR REPLACE FUNCTION mip.gold.fn_segment_counts(
  segment_codes ARRAY<STRING>,
  segment_mode  STRING,
  states        ARRAY<STRING>
)
RETURNS BIGINT
NOT DETERMINISTIC
COMMENT 'Reviewed Mortgage Growth Agent actionability tool. Counts DISTINCT clip from gold.borrower_360 after the full contact-eligibility gate (marketing eligibility, opt-in consent, suppression, do-not-contact, frequency cap). Read-only.'
RETURN (
  SELECT COUNT(DISTINCT b.clip)
  FROM mip.gold.borrower_360 AS b
  WHERE (b.marketing_eligible = TRUE
    AND b.consent_status = 'opt_in'
    AND b.suppression_reason IS NULL
    AND COALESCE(b.dnc, FALSE) = FALSE
    AND (b.eligible_recontact_at IS NULL
      OR b.eligible_recontact_at <= CURRENT_TIMESTAMP())
    AND (b.last_touch_at IS NULL
      OR b.last_touch_at < CURRENT_TIMESTAMP() - INTERVAL '30' DAYS))
    AND (
      SIZE(COALESCE(segment_codes, CAST(ARRAY() AS ARRAY<STRING>))) = 0
      OR CASE
        WHEN LOWER(COALESCE(segment_mode, 'any')) = 'all'
          THEN SIZE(ARRAY_EXCEPT(TRANSFORM(segment_codes, x -> LOWER(x)), b.segment_codes)) = 0
        ELSE SIZE(ARRAY_INTERSECT(TRANSFORM(segment_codes, x -> LOWER(x)), b.segment_codes)) > 0
      END
    )
    AND (
      SIZE(COALESCE(states, CAST(ARRAY() AS ARRAY<STRING>))) = 0
      OR ARRAY_CONTAINS(TRANSFORM(states, x -> UPPER(x)), UPPER(b.state))
    )
);
