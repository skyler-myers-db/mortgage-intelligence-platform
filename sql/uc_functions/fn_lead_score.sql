-- =============================================================================
-- fn_lead_score
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 lead-scoring primitive. Collapses five
--            component sub-scores (each 0..100) into a single integer
--            opportunity score in [0, 100] used by the Lead Queue, Segment
--            Intelligence, and Borrower 360 surfaces.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Weights (non-negotiable, from product briefing):
--              economic_incentive : 0.35
--              intent_trigger     : 0.30
--              fit                : 0.15
--              relationship       : 0.10
--              evidence           : 0.10
--
-- Rounding:  Round-half-to-even (banker's rounding) via Databricks `bround`.
--            The SQL weight literals (0.35 etc.) are DECIMAL in Spark, so
--            this function computes the weighted sum EXACTLY and brounds the
--            exact value. The Python scorer (backend/services/scoring.py)
--            MUST therefore use decimal.Decimal + ROUND_HALF_EVEN — NOT
--            float + round(), which bankers-rounds a drifted binary float
--            and diverged on ~0.67% of inputs (2026-06-11 audit P1-1; e.g.
--            (92,94,94,85,25): exact 85.5 -> 86, float 85.4999... -> 85).
--            Golden fixtures in tests/fixtures/lead_score_golden.json pin
--            both, including drift-zone case_13.
--
-- Clipping:  Final value clipped to [0, 100]. In-range inputs cannot
--            mathematically exceed 100, but the clip is defense-in-depth
--            against bad upstream data.
--
-- NULLs:     Any NULL component is coerced to 0 before weighting. A
--            completely-NULL row therefore returns 0, not NULL. Rationale:
--            "no signal" == "no opportunity" is the safer default for a
--            contact-prioritization surface, and keeps the column
--            NOT NULL for downstream ranking.
--
-- Determinism: Pure arithmetic, no nondeterministic calls. Safe for use
--            in gold materializations and metric views.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_lead_score(
  economic_incentive INT,
  intent_trigger     INT,
  fit                INT,
  relationship       INT,
  evidence           INT
)
RETURNS INT
DETERMINISTIC
COMMENT 'Module 0 canonical lead score. Weighted blend of five 0..100 sub-scores, banker-rounded and clipped to [0,100]. NULL components treated as 0. See tests/fixtures/lead_score_golden.json for parity fixtures.'
RETURN
  LEAST(
    100,
    GREATEST(
      0,
      CAST(
        BROUND(
            0.35 * COALESCE(economic_incentive, 0)
          + 0.30 * COALESCE(intent_trigger,     0)
          + 0.15 * COALESCE(fit,                0)
          + 0.10 * COALESCE(relationship,       0)
          + 0.10 * COALESCE(evidence,           0)
        )
      AS INT)
    )
  );
