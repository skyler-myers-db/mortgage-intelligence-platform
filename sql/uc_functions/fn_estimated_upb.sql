-- =============================================================================
-- fn_estimated_upb
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 estimated-current-UPB primitive. Estimates the
--            current unpaid principal balance from original first-lien UPB,
--            estimated annual note rate, and elapsed months since origination.
--            Gold borrower equity/LTV uses this estimate when the source lien
--            row has enough first-lien origination data.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Inputs:    original_upb    = original first-lien amount, USD.
--            estimated_rate  = annual note-rate estimate as a fraction
--                              (0.06 == 6.00%).
--            months_elapsed  = whole elapsed months since origination.
--
-- Term:      Fixed 360-month amortization convention. The function signature
--            deliberately stays on the three source fields available across
--            the Cotality lien feed; the gold CTAS owns month calculation.
--
-- Rounding:  Round-half-to-even (banker's rounding) via Databricks BROUND.
--            The Python mirror in backend/services/scoring.py uses round()
--            and tests/fixtures/estimated_upb_golden.json pins both ordinary
--            amortization and edge behavior.
--
-- NULLs:     Missing/nonpositive original UPB -> 0 because no principal can be
--            amortized. Missing/negative elapsed months -> 0 elapsed months.
--            Elapsed months at/past term -> 0. Missing/nonpositive rate uses
--            straight-line amortization over 360 months; this is the governed
--            unknown-rate fallback and prevents a NULL rate from erasing the
--            lien or inventing interest.
--
-- Determinism: Pure arithmetic, no nondeterministic calls. Safe for use in
--            gold materializations and metric views.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_estimated_upb(
  original_upb   BIGINT,
  estimated_rate DOUBLE,
  months_elapsed INT
)
RETURNS BIGINT
DETERMINISTIC
COMMENT 'Module 0 canonical estimated-current-UPB primitive. Estimates amortized current principal from original UPB, fractional annual note rate, and elapsed months using a 360-month convention. Unknown/nonpositive rates use straight-line fallback; missing/nonpositive original UPB returns 0. See tests/fixtures/estimated_upb_golden.json for parity fixtures.'
RETURN
  CASE
    WHEN original_upb IS NULL OR original_upb <= 0 THEN 0
    ELSE CAST(GREATEST(0.0, BROUND(
      CASE
        WHEN LEAST(360, GREATEST(0, COALESCE(months_elapsed, 0))) >= 360 THEN 0.0
        WHEN estimated_rate IS NULL OR estimated_rate <= 0 THEN
          CAST(original_upb AS DOUBLE)
          * (360 - LEAST(360, GREATEST(0, COALESCE(months_elapsed, 0))))
          / 360.0
        ELSE
          CAST(original_upb AS DOUBLE)
          * (
              POWER(1.0 + estimated_rate / 12.0, 360)
              - POWER(
                  1.0 + estimated_rate / 12.0,
                  LEAST(360, GREATEST(0, COALESCE(months_elapsed, 0)))
                )
            )
          / (POWER(1.0 + estimated_rate / 12.0, 360) - 1.0)
      END
    )) AS BIGINT)
  END;
