/**
 * Canonical opportunity-score threshold + display bands (S1).
 *
 * THE single pinned frontend site for the high-opportunity threshold and the
 * `.score--high/med/low` band edges. Parity mirrors (change all together,
 * never one alone):
 *   - sql/uc_functions/fn_high_opportunity.sql  (UC layer, threshold)
 *   - sql/uc_functions/fn_score_band.sql        (UC layer, band edges)
 *   - backend/services/scoring.py               (backend layer)
 *
 * Band edges match the design prototype exactly (design_files/Module 0
 * Prototype.html ScoreBadge: `value >= 85 ? 'high' : value >= 65 ? 'med' :
 * 'low'`). tests/unit/test_score_threshold_guard.py greps the repo so a
 * stray literal predicate fails CI and asserts the three layers agree.
 * Every other frontend consumer MUST import from here — never re-declare
 * the numbers or hard-code "75+" copy.
 */

/** Scores at or above this mark are the strongest review candidates. */
export const HIGH_OPPORTUNITY_THRESHOLD = 75;

/** `.score--high` lower edge (inclusive). */
export const SCORE_BAND_HIGH_MIN = 85;

/** `.score--med` lower edge (inclusive); below it is `.score--low`. */
export const SCORE_BAND_MED_MIN = 65;

export type ScoreBand = 'high' | 'med' | 'low';

/**
 * Canonical band assignment driving the prototype `.score--${band}` BEM
 * classes. Mirrors `mip.gold.fn_score_band` / `scoring.score_band`.
 */
export function scoreBand(value: number): ScoreBand {
  if (value >= SCORE_BAND_HIGH_MIN) return 'high';
  if (value >= SCORE_BAND_MED_MIN) return 'med';
  return 'low';
}

/** Shared "75+" copy fragment so screen text tracks the pinned threshold. */
export const HIGH_OPPORTUNITY_SCORE_LABEL = `${HIGH_OPPORTUNITY_THRESHOLD}+`;

/** Canonical KPI / filter label for the high-opportunity cut. */
export const HIGH_OPPORTUNITY_KPI_LABEL = `Opportunity score ${HIGH_OPPORTUNITY_SCORE_LABEL}`;
