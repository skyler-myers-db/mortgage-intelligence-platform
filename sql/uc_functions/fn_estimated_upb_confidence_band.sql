-- =============================================================================
-- fn_estimated_upb_confidence_band
-- -----------------------------------------------------------------------------
-- Purpose:   Deterministic confidence band for estimated current first-lien UPB.
--            The point estimate uses the same bounded rate contract as gold.
--            The lower/upper band recomputes fn_estimated_upb at the plausible
--            mortgage-rate floor/ceiling represented by fn_bounded_mortgage_rate.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Inputs:    original_upb    = original first-lien amount, USD.
--            estimated_rate  = annual note-rate estimate as a fraction.
--            months_elapsed  = whole elapsed months since origination.
--
-- Output:    STRUCT(lower_upb, estimate_upb, upper_upb), all BIGINT USD.
--            The band always contains the point estimate, including unknown-rate
--            straight-line fallback cases.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_estimated_upb_confidence_band(
  original_upb   BIGINT,
  estimated_rate DOUBLE,
  months_elapsed INT
)
RETURNS STRUCT<lower_upb: BIGINT, estimate_upb: BIGINT, upper_upb: BIGINT>
DETERMINISTIC
COMMENT 'Module 0 estimated-UPB confidence band. Point estimate composes fn_estimated_upb with fn_bounded_mortgage_rate; lower/upper recompute fn_estimated_upb at the canonical plausible rate bounds. See tests/fixtures/estimated_upb_confidence_band_golden.json for parity fixtures.'
RETURN
  named_struct(
    'lower_upb',
      LEAST(
        mip.gold.fn_estimated_upb(
          original_upb,
          mip.gold.fn_bounded_mortgage_rate(estimated_rate),
          months_elapsed
        ),
        mip.gold.fn_estimated_upb(
          original_upb,
          mip.gold.fn_bounded_mortgage_rate(0.01D),
          months_elapsed
        ),
        mip.gold.fn_estimated_upb(
          original_upb,
          mip.gold.fn_bounded_mortgage_rate(0.15D),
          months_elapsed
        )
      ),
    'estimate_upb',
      mip.gold.fn_estimated_upb(
        original_upb,
        mip.gold.fn_bounded_mortgage_rate(estimated_rate),
        months_elapsed
      ),
    'upper_upb',
      GREATEST(
        mip.gold.fn_estimated_upb(
          original_upb,
          mip.gold.fn_bounded_mortgage_rate(estimated_rate),
          months_elapsed
        ),
        mip.gold.fn_estimated_upb(
          original_upb,
          mip.gold.fn_bounded_mortgage_rate(0.01D),
          months_elapsed
        ),
        mip.gold.fn_estimated_upb(
          original_upb,
          mip.gold.fn_bounded_mortgage_rate(0.15D),
          months_elapsed
        )
      )
  );
