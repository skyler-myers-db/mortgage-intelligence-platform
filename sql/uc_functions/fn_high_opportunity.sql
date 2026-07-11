-- =============================================================================
-- fn_high_opportunity
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 high-opportunity predicate. TRUE iff the
--            opportunity score (mip.gold.fn_lead_score output) clears the
--            governed high-opportunity threshold. Every "opportunity score
--            75+" / "top-tier opportunity" / "high-opportunity borrowers"
--            aggregate in silver/gold/metric-view SQL MUST call this
--            function instead of repeating the literal threshold.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Threshold: >= 75 (inclusive). This is THE single pinned site for the
--            high-opportunity threshold in the Unity Catalog layer.
--            Parity mirrors (change all together, never one alone):
--              - backend/services/scoring.py::HIGH_OPPORTUNITY_THRESHOLD
--              - frontend/src/lib/opportunityScore.ts::HIGH_OPPORTUNITY_THRESHOLD
--            tests/unit/test_score_threshold_guard.py greps the repo so a
--            stray literal 75 predicate fails CI, and asserts the three
--            layer constants stay equal.
--
-- NULLs:     NULL score -> FALSE. "No score" must not silently count as a
--            top-tier opportunity on a contact-prioritization surface
--            (same fail-closed rationale as fn_in_the_money).
--
-- Determinism: Pure comparison, no nondeterministic calls. Safe for use
--            in gold materializations and metric views.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_high_opportunity(
  opportunity_score INT
)
RETURNS BOOLEAN
DETERMINISTIC
COMMENT 'Module 0 canonical high-opportunity predicate. TRUE iff opportunity_score >= 75 (the governed high-opportunity threshold; single pinned UC site). NULL returns FALSE. See tests/fixtures/score_band_golden.json for parity fixtures.'
RETURN
  CASE
    WHEN opportunity_score IS NULL THEN FALSE
    ELSE opportunity_score >= 75
  END;
