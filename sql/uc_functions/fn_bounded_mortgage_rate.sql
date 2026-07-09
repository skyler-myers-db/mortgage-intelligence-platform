-- =============================================================================
-- fn_bounded_mortgage_rate
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 source-quality bound for first-position
--            mortgage rates before rate spread and estimated-UPB math.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Inputs:    raw_rate = annual note-rate estimate as a fraction
--                       (0.06 == 6.00%).
--
-- Bounds:    NULL/non-finite values stay NULL. Rates below 1% are treated as
--            missing because they usually indicate unit confusion or a missing
--            source signal. Rates above 15% clamp to 15% so synthetic/source
--            outliers remain visible without dominating mortgage economics.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_bounded_mortgage_rate(raw_rate DOUBLE)
RETURNS DOUBLE
DETERMINISTIC
COMMENT 'Module 0 canonical first-position mortgage-rate quality bound. NULL, NaN, and rates below 1% return NULL; rates above 15% clamp to 15%; other fractional APR values pass through.'
RETURN
  CASE
    WHEN raw_rate IS NULL OR isnan(raw_rate) THEN NULL
    WHEN raw_rate < 0.01 THEN NULL
    WHEN raw_rate > 0.15 THEN 0.15
    ELSE raw_rate
  END;
