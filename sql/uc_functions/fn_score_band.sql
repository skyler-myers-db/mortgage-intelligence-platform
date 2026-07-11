-- =============================================================================
-- fn_score_band
-- -----------------------------------------------------------------------------
-- Purpose:   Canonical Module 0 opportunity-score band. Maps an opportunity
--            score (mip.gold.fn_lead_score output) onto the three display
--            bands the product renders as `.score--high` / `.score--med` /
--            `.score--low` (design_files/Module 0 Prototype.html ScoreBadge:
--            `value >= 85 ? 'high' : value >= 65 ? 'med' : 'low'`). Any SQL
--            that needs a band MUST call this function instead of repeating
--            the band-edge literals.
--
-- Owner:     data-modeler (Mortgage Intelligence Platform, Module 0)
--
-- Bands:     'high'  score >= 85            (inclusive)
--            'med'   65 <= score < 85       (inclusive lower edge)
--            'low'   score < 65
--            This is THE single pinned site for the band edges in the
--            Unity Catalog layer. Parity mirrors (change all together):
--              - backend/services/scoring.py::SCORE_BAND_HIGH_MIN / _MED_MIN
--              - frontend/src/lib/opportunityScore.ts::SCORE_BAND_HIGH_MIN / _MED_MIN
--            tests/unit/test_score_threshold_guard.py greps the repo for
--            stray band-edge predicates and asserts the layer constants
--            stay equal. Golden edge cases (64/65/66, 84/85/86) live in
--            tests/fixtures/score_band_golden.json.
--
-- Note:      The high-opportunity threshold (75, fn_high_opportunity) is a
--            SEPARATE governed constant: it marks "strongest review
--            candidates", while the bands drive score-badge display tiers.
--
-- NULLs:     NULL score -> 'low'. Matches fn_lead_score's "no signal == no
--            opportunity" coercion (a completely-NULL score row scores 0,
--            which is 'low').
--
-- Determinism: Pure comparison, no nondeterministic calls. Safe for use
--            in gold materializations and metric views.
-- =============================================================================

CREATE OR REPLACE FUNCTION mip.gold.fn_score_band(
  opportunity_score INT
)
RETURNS STRING
DETERMINISTIC
COMMENT 'Module 0 canonical opportunity-score band: high (>= 85), med (>= 65), low (else; NULL coerces to low). Single pinned UC site for the band edges; mirrors the prototype ScoreBadge tiers. See tests/fixtures/score_band_golden.json for parity fixtures.'
RETURN
  CASE
    WHEN COALESCE(opportunity_score, 0) >= 85 THEN 'high'
    WHEN COALESCE(opportunity_score, 0) >= 65 THEN 'med'
    ELSE 'low'
  END;
