/**
 * Rate Lever read model (audit wow-stage-1): pure functions over the
 * `GET /api/geo/rate-sensitivity` payload. Static (it ships with the map) so
 * the fill, the table and the legend recount without loading the lazy
 * control chunk; the control's own recounts live in `rateScenario.recount`.
 *
 * The client does no rate arithmetic: every scenario par rate comes from the
 * server (`scenario_market_rate_pct`), and every count is a server count at a
 * grid step, aligned to `steps_bps` (the repository drops a state with an
 * incomplete grid rather than zero-filling it). The fill uses ONE scale over
 * every state's count at every step, so the breaks stay fixed while scrubbing
 * and a colour change always means a count change.
 */
import type { RateSensitivityResponse, RateSensitivityState } from '../../types/rateScenario';
import { SQRT_SCALE_MAX_UNITS, buildChoroplethScale, type ChoroplethScale } from './USChoroplethMap.scale';

export interface RateScenarioIndex {
  response: RateSensitivityResponse;
  steps: readonly number[];
  /** Keyed by lowercase USPS code, the map's location ids. */
  byState: Readonly<Record<string, RateSensitivityState>>;
  /** ONE scale over every state's count at every step (see `fixedScenarioScale`). */
  scale: ChoroplethScale | null;
}

/** What the fill, the table and the legend total show at one step. */
export interface MapScenarioView {
  step: number;
  /** The server's scenario par rate at `step`, in percent. */
  ratePct: number;
  /** In the money at `step`, per lowercase state id. */
  inTheMoneyById: Readonly<Record<string, number>>;
  /** In the money at `step` over the whole book: the legend's national recount. */
  total: number;
}

/**
 * ONE scale over every state's count at every step. The scale kind follows
 * the number of populated states exactly as the borrower fill does (a square
 * root over the grid maximum below SQRT_SCALE_MAX_UNITS states, quartiles of
 * every state-step count above it), so the breaks never move while scrubbing.
 */
export function fixedScenarioScale(states: readonly RateSensitivityState[]): ChoroplethScale | null {
  const pooled = states.flatMap((state) => state.in_the_money.filter((count) => count > 0));
  const populated = states.filter((state) => state.in_the_money.some((count) => count > 0)).length;
  const scale = buildChoroplethScale(populated < SQRT_SCALE_MAX_UNITS ? [Math.max(0, ...pooled)] : pooled);
  return scale && { ...scale, units: populated };
}

/** Index a built grid; null when it is not built or has no state. */
export function indexRateScenario(response: RateSensitivityResponse | null): RateScenarioIndex | null {
  if (!response?.built || response.states.length === 0) return null;
  const byState: Record<string, RateSensitivityState> = {};
  for (const state of response.states) byState[state.state.toLowerCase()] = state;
  return { response, steps: response.steps_bps, byState, scale: fixedScenarioScale(response.states) };
}

/** Everything the painted view needs at `step`; null off the grid. */
export function scenarioView(index: RateScenarioIndex, step: number): MapScenarioView | null {
  const at = index.steps.indexOf(step);
  const ratePct = index.response.scenario_market_rate_pct[at];
  if (ratePct === undefined) return null;
  const inTheMoneyById: Record<string, number> = {};
  let total = 0;
  for (const id in index.byState) total += inTheMoneyById[id] = index.byState[id].in_the_money[at] ?? 0;
  return { step, ratePct, inTheMoneyById, total };
}
