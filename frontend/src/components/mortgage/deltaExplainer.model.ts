/**
 * The Delta Explainer's waterfall model (audit 2026-09-21 `wow-ai-3`), pure:
 * the baseline total, the six states that moved most, one "Other states"
 * bucket, the change no state row accounts for, then the current total.
 * Every value is a server count; the steps only regroup them, so the bars
 * and the table always reconcile to `total_change`.
 */
import type { HomeAttributionState, HomeSummaryAttributionResponse } from '../../types/homeAttribution';

/** States drawn as their own bar; the rest share "Other states". */
export const TOP_STATE_BARS = 6;
export const OTHER_STATES_LABEL = 'Other states';
export const UNATTRIBUTED_LABEL = 'Not attributed to a state';

export type WaterfallKind = 'total' | 'state' | 'other' | 'unattributed';

export interface WaterfallStep {
  kind: WaterfallKind;
  label: string;
  /** The state code for a state step. */
  state?: string;
  /** A total's value, or a step's signed change. */
  value: number;
  /** Where the bar starts and ends on the running total. */
  from: number;
  to: number;
  /** Table cells: the counts at baseline and now (null for buckets without one). */
  baselineCount: number | null;
  currentCount: number | null;
}

/** The steps, or null when the snapshots cannot reconcile (no totals). */
export function waterfallSteps(response: HomeSummaryAttributionResponse): WaterfallStep[] | null {
  const { baseline_total: baseline, current_total: current, total_change: total } = response;
  if (baseline === null || current === null || total === null) return null;
  const moved = response.states.filter((row): row is HomeAttributionState & { change: number } => row.change !== null);
  const top = moved.filter((row) => row.change !== 0).slice(0, TOP_STATE_BARS);
  const rest = moved.filter((row) => !top.includes(row));
  const steps: WaterfallStep[] = [
    { kind: 'total', label: 'At baseline', value: baseline, from: 0, to: baseline, baselineCount: baseline, currentCount: null },
  ];
  let running = baseline;
  const push = (step: Omit<WaterfallStep, 'from' | 'to'>) => {
    steps.push({ ...step, from: running, to: running + step.value });
    running += step.value;
  };
  for (const row of top) {
    push({ kind: 'state', label: row.state, state: row.state, value: row.change, baselineCount: row.baseline_count, currentCount: row.current_count });
  }
  const otherChange = rest.reduce((sum, row) => sum + row.change, 0);
  if (rest.length > 0) {
    push({ kind: 'other', label: OTHER_STATES_LABEL, value: otherChange, baselineCount: null, currentCount: null });
  }
  const unattributed = response.unattributed_change ?? 0;
  if (unattributed !== 0) {
    push({ kind: 'unattributed', label: UNATTRIBUTED_LABEL, value: unattributed, baselineCount: null, currentCount: null });
  }
  steps.push({ kind: 'total', label: 'Now', value: current, from: 0, to: current, baselineCount: null, currentCount: current });
  return steps;
}

/**
 * The drawn domain: the running totals with a margin, so a change of a few
 * hundred on a book of a million is visible. It does not start at zero; the
 * explainer says so beside the chart.
 */
export function waterfallDomain(steps: readonly WaterfallStep[]): { lo: number; hi: number } {
  const points = steps.flatMap((step) => (step.kind === 'total' ? [step.to] : [step.from, step.to]));
  const min = Math.min(...points);
  const max = Math.max(...points);
  const pad = Math.max(1, Math.round((max - min) * 0.15));
  return { lo: Math.max(0, min - pad), hi: max + pad };
}
