/**
 * Frontend leg of the S1 canonical-score parity contract. The band edges and
 * the high-opportunity threshold are pinned by tests/fixtures/
 * score_band_golden.json — the SAME fixture that pins
 * mip.gold.fn_score_band / fn_high_opportunity (sql/fixtures/
 * score_band_validation.sql) and backend/services/scoring.py
 * (tests/unit/test_scoring.py). tests/unit/test_score_threshold_guard.py
 * asserts the exported constants match the other layers numerically.
 */

import { describe, expect, it } from 'vitest';
import goldenFixture from '../../../tests/fixtures/score_band_golden.json';
import {
  HIGH_OPPORTUNITY_KPI_LABEL,
  HIGH_OPPORTUNITY_SCORE_LABEL,
  HIGH_OPPORTUNITY_THRESHOLD,
  SCORE_BAND_HIGH_MIN,
  SCORE_BAND_MED_MIN,
  scoreBand,
} from './opportunityScore';

interface GoldenCase {
  id: string;
  inputs: { opportunity_score: number | null };
  expected_band: 'high' | 'med' | 'low';
  expected_high_opportunity: boolean;
}

const golden = goldenFixture as unknown as { cases: GoldenCase[] };

describe('scoreBand golden parity', () => {
  it('covers the 74/75/76, 84/85/86, and 64/65/66 edge triplets', () => {
    const scores = golden.cases.map((c) => c.inputs.opportunity_score);
    for (const edge of [64, 65, 66, 74, 75, 76, 84, 85, 86]) {
      expect(scores).toContain(edge);
    }
  });

  for (const goldenCase of golden.cases) {
    it(goldenCase.id, () => {
      const score = goldenCase.inputs.opportunity_score;
      // NULL coerces to 0 in SQL/Python; the UI never renders a null score
      // badge, so the frontend contract is the numeric coercion.
      expect(scoreBand(score ?? 0)).toBe(goldenCase.expected_band);
      expect((score ?? 0) >= HIGH_OPPORTUNITY_THRESHOLD).toBe(
        goldenCase.expected_high_opportunity,
      );
    });
  }
});

describe('band mapping drives the prototype .score--* classes', () => {
  it('assigns high at the inclusive high edge', () => {
    expect(scoreBand(SCORE_BAND_HIGH_MIN)).toBe('high');
    expect(scoreBand(SCORE_BAND_HIGH_MIN - 1)).toBe('med');
  });

  it('assigns med at the inclusive med edge', () => {
    expect(scoreBand(SCORE_BAND_MED_MIN)).toBe('med');
    expect(scoreBand(SCORE_BAND_MED_MIN - 1)).toBe('low');
  });
});

describe('threshold copy tracks the pinned constant', () => {
  it('renders the "N+" fragment from the constant', () => {
    expect(HIGH_OPPORTUNITY_SCORE_LABEL).toBe(`${HIGH_OPPORTUNITY_THRESHOLD}+`);
    expect(HIGH_OPPORTUNITY_KPI_LABEL).toBe(
      `Opportunity score ${HIGH_OPPORTUNITY_THRESHOLD}+`,
    );
  });
});
