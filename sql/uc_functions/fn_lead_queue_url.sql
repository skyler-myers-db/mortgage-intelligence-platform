-- =============================================================================
-- fn_lead_queue_url
-- -----------------------------------------------------------------------------
-- Purpose:   Reviewed UC-function tool contract for Growth Agent handoffs.
--            Builds a public, app-internal Lead Queue route from reviewed
--            segment/state filters. It never sends outreach or writes state.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_lead_queue_url(
  segment_codes ARRAY<STRING>,
  segment_mode  STRING,
  states        ARRAY<STRING>
)
RETURNS STRING
DETERMINISTIC
COMMENT 'Reviewed Mortgage Growth Agent Lead Queue handoff tool. Produces a safe app route from reviewed filters; no outreach or state write.'
RETURN CONCAT(
  '/lead-queue?',
  CASE
    WHEN SIZE(COALESCE(segment_codes, CAST(ARRAY() AS ARRAY<STRING>))) = 1
      THEN CONCAT('segment=', LOWER(segment_codes[0]))
    WHEN SIZE(COALESCE(segment_codes, CAST(ARRAY() AS ARRAY<STRING>))) > 1
      THEN CONCAT(
        'segment_codes=',
        ARRAY_JOIN(TRANSFORM(segment_codes, x -> LOWER(x)), ','),
        '&segment_mode=',
        CASE WHEN LOWER(COALESCE(segment_mode, 'any')) = 'all' THEN 'all' ELSE 'any' END
      )
    ELSE 'source=trusted_sql'
  END,
  '&marketing_eligibility=Eligible+only',
  CASE
    WHEN SIZE(COALESCE(states, CAST(ARRAY() AS ARRAY<STRING>))) > 0
      THEN CONCAT('&states=', ARRAY_JOIN(TRANSFORM(states, x -> UPPER(x)), ','))
    ELSE ''
  END
);
