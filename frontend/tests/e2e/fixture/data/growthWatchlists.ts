/**
 * Fixtures for the W5d Growth Agent watchlists lane (audit 2026-09-21
 * `wow-ai-2`, `genie-09` part 4): "Watch this level" on the Rate Lever
 * (`POST /api/growth-agent/rate-watch`: 200, the grid-changed 409 and a 429
 * with a Retry-After) and a composed plan's per-step progress
 * (`GET /api/growth-agent/agent/plan/progress`).
 *
 * The one default read is the progress poll answering `not_started`: the
 * composed-plan card polls it while ANY spec's execute is pending, and the
 * harness fails an unhandled /api call. Specs register the rest per test.
 * The level comes from the shared RATE_LEVER grid, never from the browser:
 * the server reads it, so the watch reply carries it.
 *
 * Synthetic only; imports from frontend/src are type-only.
 */
import type {
  GrowthAgentPlanProgressResponse,
  GrowthAgentRateWatchRequest,
  GrowthAgentRateWatchResponse,
} from '../../../../src/types/growthAgent';
import type { ContractSample } from '../contractSamples';
import { fixture, json, type FixtureEntry, type MockApi } from '../mockApi';
import { RATE_LEVER, rateLeverAt } from './rateLever';
import { SNAPSHOT_AT } from './reference';

export const RATE_WATCH_PATTERN = '/api/growth-agent/rate-watch';
export const PLAN_PROGRESS_PATTERN = '/api/growth-agent/agent/plan/progress';
/** The server's 409 when the step is no longer on today's grid. */
export const RATE_WATCH_GRID_CHANGED = { detail: 'The Rate Lever grid changed; move the slider again.' };
export const RATE_WATCH_RETRY_AFTER_S = 30;
/** The backpressure 429 (mutation budget): longer than the transport's 2 s inner cap, so it surfaces. */
export const RATE_WATCH_THROTTLED = {
  detail: 'Request budget exceeded (fixture growth-watchlists detail).',
  retryable: true,
  dependency: 'lakebase',
  reason: 'rate_limited',
  retry_after_seconds: RATE_WATCH_RETRY_AFTER_S,
};

export const NOT_STARTED: GrowthAgentPlanProgressResponse = {
  state: 'not_started', step_index: null, step_total: null, step_tool: null, updated_at: null,
};

function progressAt(
  state: GrowthAgentPlanProgressResponse['state'],
  step: number,
  tool: string,
): GrowthAgentPlanProgressResponse {
  return { state, step_index: step, step_total: 3, step_tool: tool, updated_at: SNAPSHOT_AT };
}

/** The reviewed plan's progress (data/growthAgentTrust REVIEWED_PLAN): two reads, the gate, recording. */
export const PROGRESS_SEQUENCE: readonly GrowthAgentPlanProgressResponse[] = [
  progressAt('running', 1, 'fn_build_cohort'),
  progressAt('running', 2, 'fn_segment_counts'),
  progressAt('gate', 3, 'fn_lead_queue_url'),
  progressAt('recording', 2, 'fn_lead_queue_url'),
];

const DIRECTION_WORDS = { at_or_below: 'at or below', at_or_above: 'at or above' } as const;

/** The POST /rate-watch 200 for a step of the shared grid. */
export function rateWatchSaved(step: number, replayed = false): GrowthAgentRateWatchResponse {
  const level = RATE_LEVER.scenario_market_rate_pct[rateLeverAt(step)];
  const direction = step < 0 ? 'at_or_below' : 'at_or_above';
  return {
    monitor: {
      monitor_id: '5a1e0000-0000-4000-8000-00000000d5a1',
      workflow_id: 'rate_trigger_watch',
      name: `Rate watch - 30-year par ${DIRECTION_WORDS[direction]} ${level.toFixed(2)} percent`,
      cadence: 'daily',
      status: 'active',
      criteria: {
        states: [],
        rate_trigger: {
          series_id: 'MORTGAGE30US', step_bps: step, level_pct: level, direction,
          base_market_rate_pct: RATE_LEVER.base_market_rate_pct,
        },
        marketing_eligibility: 'Eligible only',
        workflow_id: 'rate_trigger_watch',
      },
      route: `/?map_mode=rate&rate_step=${step}`,
      actionable_total: 0,
      source_assets: ['mip.gold.rate_window_weekly', 'mip.gold.rate_sensitivity_rollup'],
      last_run_id: null,
      created_at: SNAPSHOT_AT,
      updated_at: SNAPSHOT_AT,
    },
    replayed,
    watch_level_pct: level,
    watch_direction: direction,
    latest_print_pct: 6.12,
    latest_print_week: '2026-09-28',
    scheduler: { state: 'paused', reason: 'job_schedule' },
  };
}

export type RateWatchOutcome = 'saved' | 'grid-changed' | 'throttled';

export interface RateWatchRecorder {
  /** Every body the browser posted to /rate-watch. */
  readonly bodies: GrowthAgentRateWatchRequest[];
}

/** Register POST /rate-watch; `outcomes` answer in order, the last one repeating. */
export function registerRateWatch(mockApi: MockApi, outcomes: RateWatchOutcome[] = ['saved']): RateWatchRecorder {
  const bodies: GrowthAgentRateWatchRequest[] = [];
  mockApi.register<GrowthAgentRateWatchResponse | typeof RATE_WATCH_GRID_CHANGED | typeof RATE_WATCH_THROTTLED>('POST', RATE_WATCH_PATTERN, (request) => {
    const body = request.body as GrowthAgentRateWatchRequest;
    bodies.push(body);
    const outcome = outcomes[Math.min(bodies.length, outcomes.length) - 1];
    if (outcome === 'grid-changed') return { status: 409, body: RATE_WATCH_GRID_CHANGED };
    if (outcome === 'throttled') {
      return { status: 429, headers: { 'Retry-After': String(RATE_WATCH_RETRY_AFTER_S) }, body: RATE_WATCH_THROTTLED };
    }
    return { body: rateWatchSaved(body.step_bps) };
  });
  return { bodies };
}

/** The default poll answer: no progress row yet. */
export const growthWatchlistFixtures: FixtureEntry[] = [
  fixture('GET', PLAN_PROGRESS_PATTERN, () => json<GrowthAgentPlanProgressResponse>(NOT_STARTED)),
];

/** Curated bodies for the fixture contract exporter. */
export function contractSamples(): ContractSample[] {
  const progress = (source: string, body: GrowthAgentPlanProgressResponse): ContractSample => ({
    source, method: 'GET', pattern: PLAN_PROGRESS_PATTERN, path: PLAN_PROGRESS_PATTERN,
    query: 'request_id=5a1e0000-0000-4000-8000-0000000000bb', status: 200, body,
  });
  const watch = (source: string, body: unknown, status = 200): ContractSample => ({
    source, method: 'POST', pattern: RATE_WATCH_PATTERN, path: RATE_WATCH_PATTERN, query: '', status, body,
  });
  return [
    watch('rateWatchSaved(-50)', rateWatchSaved(-50)),
    watch('rateWatchSaved(50, replayed)', rateWatchSaved(50, true)),
    watch('RATE_WATCH_GRID_CHANGED', RATE_WATCH_GRID_CHANGED, 409),
    progress('NOT_STARTED', NOT_STARTED),
    ...PROGRESS_SEQUENCE.map((body, index) => progress(`PROGRESS_SEQUENCE[${index}]`, body)),
  ];
}
