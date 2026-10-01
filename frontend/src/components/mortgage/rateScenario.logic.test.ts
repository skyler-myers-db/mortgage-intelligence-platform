import { describe, expect, it } from 'vitest';
import type { RateSensitivityResponse, RateSensitivityState } from '../../types/rateScenario';
import { classify } from './USChoroplethMap.scale';
import { RATE_COHORT_NOTE, fixedScenarioScale, indexRateScenario, scenarioView } from './rateScenario.logic';
import { nearestStep, recountAt } from './rateScenario.recount';

const STEPS = [-100, -75, -50, -25, 0, 25, 50, 75, 100];

function state(code: string, inTheMoney: number[], contactable: number[] | null = null): RateSensitivityState {
  return { state: code, addressable: 10_000, rate_movable: 8_000, in_the_money: inTheMoney, contactable_in_the_money: contactable };
}

function response(states: RateSensitivityState[], built = true): RateSensitivityResponse {
  return {
    built,
    steps_bps: built ? STEPS : [],
    scenario_market_rate_pct: built ? STEPS.map((step) => 6.3 + step / 100) : [],
    base_market_rate_pct: built ? 6.3 : null,
    thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
    states,
    provenance: {
      gold_source: 'mip.gold.rate_sensitivity_rollup',
      book_source: 'b',
      rule_source: 'r',
      contactable_source: 'c',
      note: 'n',
    },
  };
}

const IL = state('IL', [900, 800, 700, 600, 500, 400, 300, 200, 100], [90, 80, 70, 60, 50, 40, 30, 20, 10]);
const TX = state('TX', [450, 400, 350, 300, 250, 200, 150, 100, 50], [45, 40, 35, 30, 25, 20, 15, 10, 5]);

describe('rateScenario.logic', () => {
  it('indexes a built grid by lowercase state id and refuses an unbuilt or empty one', () => {
    const index = indexRateScenario(response([IL, TX]));
    expect(Object.keys(index?.byState ?? {})).toEqual(['il', 'tx']);
    expect(indexRateScenario(response([], false))).toBeNull();
    expect(indexRateScenario(response([]))).toBeNull();
    expect(indexRateScenario(null)).toBeNull();
  });

  it('reads the value per state at a step, with the server rate', () => {
    const index = indexRateScenario(response([IL, TX]));
    if (!index) throw new Error('index');
    const view = scenarioView(index, -50);
    expect(view?.ratePct).toBeCloseTo(5.8);
    expect(view?.inTheMoneyById).toEqual({ il: 700, tx: 350 });
    expect(view?.total).toBe(1_050);
    expect(scenarioView(index, 10)).toBeNull();
  });

  it('recounts the whole book or one state', () => {
    const index = indexRateScenario(response([IL, TX]));
    if (!index) throw new Error('index');
    const view = scenarioView(index, 25);
    expect(view?.total).toBe(600);
    expect(view?.inTheMoneyById.tx).toBe(200);
    expect(view?.inTheMoneyById.ca).toBeUndefined();
    expect(recountAt(index, -100, null)).toEqual({ inTheMoney: 1350, today: 750, contactable: 135 });
    expect(recountAt(index, -100, 'il')).toEqual({ inTheMoney: 900, today: 500, contactable: 90 });
  });

  it('reports a null contactable when any state in scope does not report it, never zero', () => {
    const index = indexRateScenario(response([IL, state('TX', TX.in_the_money, null)]));
    if (!index) throw new Error('index');
    expect(recountAt(index, 0, null)?.contactable).toBeNull();
    expect(recountAt(index, 0, 'il')?.contactable).toBe(50);
  });

  // wow-stage-1 / D-dataviz-geo-b: change versus today, as numbers. The grid
  // is monotone (tests/integration/test_rate_sensitivity_parity.py::
  // test_in_the_money_never_rises_with_par), so every change in one frame
  // has the sign of the step's opposite: no diverging ramp is needed.
  const PR = state('PR', [70, 60, 50, 40, 30, 20, 10, 5, 0], null);

  it('keeps every fixture state non-increasing along the steps (the premise of single-signed change)', () => {
    for (const fixture of [IL, TX, PR]) {
      for (let i = 1; i < fixture.in_the_money.length; i += 1) {
        expect(fixture.in_the_money[i]).toBeLessThanOrEqual(fixture.in_the_money[i - 1]);
      }
    }
  });

  it('reads change versus today per state, signed against the step', () => {
    const index = indexRateScenario(response([IL, TX, PR]));
    if (!index) throw new Error('index');
    for (const step of STEPS) {
      const view = scenarioView(index, step);
      if (!view?.changeById || !view.todayById) throw new Error('change');
      expect(view.todayById).toEqual({ il: 500, tx: 250, pr: 30 });
      for (const change of Object.values(view.changeById)) {
        expect([-Math.sign(step), 0]).toContain(Math.sign(change) + 0);
      }
    }
    const lower = scenarioView(index, -50);
    expect(lower?.changeById).toEqual({ il: 200, tx: 100, pr: 20 });
    expect(scenarioView(index, 50)?.changeById).toEqual({ il: -200, tx: -100, pr: -20 });
    expect(scenarioView(index, 0)?.changeById).toEqual({ il: 0, tx: 0, pr: 0 });
  });

  it('sums the whole-book change over every grid state, PR included, equal to the recount delta', () => {
    const index = indexRateScenario(response([IL, TX, PR]));
    if (!index) throw new Error('index');
    for (const step of STEPS) {
      const recount = recountAt(index, step, null);
      if (!recount) throw new Error('recount');
      expect(scenarioView(index, step)?.totalChange).toBe(recount.inTheMoney - recount.today);
    }
    expect(scenarioView(index, -100)?.totalChange).toBe(640);
  });

  it('carries the contactable subset at the step, null per state where it is not reported', () => {
    const index = indexRateScenario(response([IL, PR]));
    if (!index) throw new Error('index');
    expect(scenarioView(index, -25)?.contactableById).toEqual({ il: 60, pr: null });
  });

  it('reports no change at all when the grid has no step 0', () => {
    const shifted = response([IL]);
    shifted.steps_bps = [-100, -75, -50, -25, 10, 25, 50, 75, 100];
    const index = indexRateScenario(shifted);
    if (!index) throw new Error('index');
    const view = scenarioView(index, -50);
    expect(view?.inTheMoneyById).toEqual({ il: 700 });
    expect(view?.changeById).toBeNull();
    expect(view?.todayById).toBeNull();
    expect(view?.contactableById).toBeNull();
    expect(view?.totalChange).toBeNull();
  });

  it("states the cohort note once, as the Lead Queue's par", () => {
    expect(RATE_COHORT_NOTE).toBe("The Lead Queue and campaigns use today's par rate.");
  });

  it('snaps a value to the nearest grid step', () => {
    expect(nearestStep(STEPS, -49)).toBe(-50);
    expect(nearestStep(STEPS, 12.5)).toBe(0);
    expect(nearestStep(STEPS, 400)).toBe(100);
  });

  it('builds ONE scale over every state at every step, so the breaks never move with the step', () => {
    const scale = fixedScenarioScale([IL, TX]);
    if (!scale) throw new Error('scale');
    // Two states: the square root over the GRID maximum (IL at -100), not the step's maximum.
    expect(scale.kind).toBe('sqrt');
    expect(scale.max).toBe(900);
    expect(scale.breaks).toEqual([57, 225, 507]);
    // A per-step scale would re-cut the classes at every step; this one does
    // not, so Illinois changes class only because its count changed.
    expect(classify(scale, IL.in_the_money[4])).toBe(3);
    expect(classify(scale, IL.in_the_money[0])).toBe(4);
    const index = indexRateScenario(response([IL, TX]));
    expect(index?.scale).toEqual(scale);
  });

  it('uses quartiles of every state-step count once enough states are populated', () => {
    const many = Array.from({ length: 12 }, (_, i) => state(`S${i}`, STEPS.map((_, s) => (i + 1) * (9 - s))));
    const scale = fixedScenarioScale(many);
    expect(scale?.kind).toBe('quantile');
    expect(scale?.units).toBe(12);
  });
});
