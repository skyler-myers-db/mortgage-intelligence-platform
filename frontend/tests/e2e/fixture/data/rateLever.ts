/**
 * Rate Lever fixture (`GET /api/geo/rate-sensitivity`, audit wow-stage-1).
 *
 * Derived from the shared reference STATES (read-only): at step 0 every
 * state's in_the_money equals its reference `inTheMoney`, so the legend at
 * today's par equals TOTALS.inTheMoney and the Home headline KPIs. Other
 * steps scale that count by a fixed, monotone factor (lower par, more
 * borrowers), capped at the state's rate-movable count, so the series is
 * non-increasing as par rises, like the real grid.
 *
 * Eight states is under SQRT_SCALE_MAX_UNITS, so the fixed scale is the
 * square root over the grid maximum (Illinois at -100). The designated state
 * (Illinois) crosses a class break between step 0 and -100: class 3 today,
 * class 4 at -100 bps.
 */
import type { RateSensitivityResponse, RateSensitivityState } from '../../../../src/types/rateScenario';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { SNAPSHOT_AT, STATES } from './reference';

export const RATE_LEVER_STEPS = [-100, -75, -50, -25, 0, 25, 50, 75, 100] as const;
/** In the money relative to today, per step (aligned to RATE_LEVER_STEPS). */
const STEP_FACTORS = [2, 1.7, 1.45, 1.2, 1, 0.82, 0.66, 0.52, 0.4] as const;
/** Today's par, in percent; each step adds step / 100 points (server-supplied in the payload). */
export const RATE_LEVER_BASE_PCT = 6.3;
export const RATE_LEVER_DESIGNATED = 'il';

function rateMovable(addressable: number): number {
  return Math.round(addressable * 0.8);
}

function stateRow(state: (typeof STATES)[number]): RateSensitivityState {
  const movable = rateMovable(state.addressable);
  const inTheMoney = STEP_FACTORS.map((factor) => Math.min(movable, Math.round(state.inTheMoney * factor)));
  const contactShare = state.contactable / state.addressable;
  return {
    state: state.code,
    addressable: state.addressable,
    rate_movable: movable,
    in_the_money: inTheMoney,
    contactable_in_the_money: inTheMoney.map((count) => Math.round(count * contactShare)),
  };
}

export const RATE_LEVER: RateSensitivityResponse = {
  built: true,
  steps_bps: [...RATE_LEVER_STEPS],
  scenario_market_rate_pct: RATE_LEVER_STEPS.map((step) => Number((RATE_LEVER_BASE_PCT + step / 100).toFixed(2))),
  base_market_rate_pct: RATE_LEVER_BASE_PCT,
  thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
  states: STATES.map(stateRow),
  provenance: {
    gold_source: 'mip.gold.rate_sensitivity_rollup',
    book_source: 'mip.gold.borrower_360 + mip.silver.lien_current',
    rule_source: 'mip.gold.fn_rate_spread + mip.gold.fn_in_the_money',
    contactable_source: 'mip.gold.borrower_360 (live eligibility predicate, per request)',
    book_as_of: SNAPSHOT_AT,
    refreshed_at: SNAPSHOT_AT,
    note: 'A scenario, not a forecast (fixture).',
  },
};

/** The payload before the gold refresh has built the grid. */
export const RATE_LEVER_NOT_BUILT: RateSensitivityResponse = {
  built: false,
  steps_bps: [],
  scenario_market_rate_pct: [],
  base_market_rate_pct: null,
  // The server's RateSensitivityThresholds() serializes both keys as null.
  thresholds: { min_spread_bps: null, min_equity_pct: null },
  states: [],
  provenance: { ...RATE_LEVER.provenance, book_as_of: null, refreshed_at: null },
};

/** Position of a step on the grid. */
export function rateLeverAt(step: number): number {
  return RATE_LEVER_STEPS.indexOf(step as (typeof RATE_LEVER_STEPS)[number]);
}

/** Whole-book in the money / contactable at a step, as the legend and headline state them. */
export function rateLeverTotals(step: number): { inTheMoney: number; contactable: number } {
  const at = rateLeverAt(step);
  return RATE_LEVER.states.reduce(
    (sum, state) => ({
      inTheMoney: sum.inTheMoney + state.in_the_money[at],
      contactable: sum.contactable + (state.contactable_in_the_money?.[at] ?? 0),
    }),
    { inTheMoney: 0, contactable: 0 },
  );
}

export const rateLeverFixtures: FixtureEntry[] = [
  fixture('GET', '/api/geo/rate-sensitivity', () => json<RateSensitivityResponse>(RATE_LEVER)),
];

/** Curated bodies for the fixture contract exporter (w3-api-contract). */
export function contractSamples() {
  return [
    { method: 'GET', pattern: '/api/geo/rate-sensitivity', path: '/api/geo/rate-sensitivity', query: '', status: 200, body: RATE_LEVER },
    { method: 'GET', pattern: '/api/geo/rate-sensitivity', path: '/api/geo/rate-sensitivity', query: '', status: 200, body: RATE_LEVER_NOT_BUILT },
  ];
}
