/**
 * Rate Lever recounts for the control (audit wow-stage-1). Lazy-only: only
 * RateScenarioControl imports this module, so it ships in the control's chunk
 * and not in the map chunk the Home and Segment Intelligence routes load (the
 * map's own fill and legend total come from `rateScenario.logic`).
 */
import type { RateScenarioIndex } from './rateScenario.logic';

/** The grid step closest to `value` (ties go to the lower step). */
export function nearestStep(steps: readonly number[], value: number): number {
  let best = steps[0] ?? 0;
  for (const step of steps) {
    if (Math.abs(step - value) < Math.abs(best - value)) best = step;
  }
  return best;
}

export interface ScenarioRecount {
  inTheMoney: number;
  /** The same scope at step 0: "today". */
  today: number;
  /** Contactable subset at `step`; null when any state in scope does not report it. */
  contactable: number | null;
}

/** Whole-book (stateId null) or one-state recount at `step`; null when the scope has no grid. */
export function recountAt(index: RateScenarioIndex, step: number, stateId: string | null): ScenarioRecount | null {
  const at = index.steps.indexOf(step);
  const today = index.steps.indexOf(0);
  if (at < 0 || today < 0) return null;
  const scope = stateId === null ? Object.values(index.byState) : [index.byState[stateId]].filter(Boolean);
  if (scope.length === 0) return null;
  let inTheMoney = 0;
  let todayCount = 0;
  let contactable: number | null = 0;
  for (const state of scope) {
    inTheMoney += state.in_the_money[at] ?? 0;
    todayCount += state.in_the_money[today] ?? 0;
    const subset = state.contactable_in_the_money?.[at];
    contactable = contactable === null || typeof subset !== 'number' ? null : contactable + subset;
  }
  return { inTheMoney, today: todayCount, contactable };
}
