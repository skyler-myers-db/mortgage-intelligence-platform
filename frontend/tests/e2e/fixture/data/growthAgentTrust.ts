/**
 * Fixtures for the Growth Agent trust lane (audit 2026-09-21 `critic-01`,
 * `genie-09`, `flow-08`, `wow-ai-4`): compose returns a plan the server signed,
 * execute runs exactly that plan (or answers 409), the runs list and the
 * watchlist summary are audit-free reads, and Save as watchlist saves exactly
 * one executed run (or answers 409).
 *
 * Nothing here is a default read, so nothing is added to registry.ts: the
 * spec registers compose and execute per test with `registerGrowthAgentTrust`,
 * which records every request body so the spec can prove what was posted.
 *
 * Synthetic only: counts come from the shared footprint (reference.ts), the
 * tools are the registry's planner-exposed tools, and no borrower id, name or
 * contact field appears. Imports from frontend/src are type-only.
 */
import type {
  ComposePlanResponse,
  ComposedPlan,
  ExecutePlanRequest,
  GrowthAgentHomeResponse,
  GrowthAgentMonitor,
  GrowthAgentPromptRunRequest,
  GrowthAgentRunResponse,
  GrowthAgentRunSummary,
  GrowthAgentRunWatchlistRequest,
  GrowthAgentSchedulerState,
  GrowthAgentWatchlistSummaryResponse,
} from '../../../../src/types/growthAgent';
import type { ContractSample } from '../contractSamples';
import type { MockApi } from '../mockApi';
import { growthRunFixture } from './askGenieRoute';
import { GROWTH_AGENT_HOME } from './genie';
import { SNAPSHOT_AT, TOTALS } from './reference';

export const COMPOSE_PATTERN = '/api/growth-agent/agent/compose';
export const EXECUTE_PATTERN = '/api/growth-agent/agent/plan/execute';
export const RUNS_PATTERN = '/api/growth-agent/runs';
export const AGENT_RUN_PATTERN = '/api/growth-agent/agent/run';
export const RUN_SAVE_PATTERN = '/api/growth-agent/runs/:runId/monitors';
export const WATCHLIST_SUMMARY_PATTERN = '/api/growth-agent/monitors/summary';
export const HOME_PATTERN = '/api/growth-agent';
/** A UUID-shaped run id for the contract exporter (the route types run_id as a UUID). */
const CONTRACT_RUN_ID = '0f0e0d0c-0b0a-4908-8706-050403020100';

export const DIGEST_REVIEWED = `v1.v1.1790000000.${'A'.repeat(43)}`;
export const DIGEST_RECOMPOSED = `v1.v1.1790000300.${'B'.repeat(43)}`;
const BORROWER_360 = 'mip.gold.borrower_360';
const EVIDENCE_EVENTS = 'mip.gold.evidence_events';
const HASH = 'd'.repeat(64);

/** Screen, gate, then hand off for review: the run stops at step 3. */
export const REVIEWED_PLAN: ComposedPlan = {
  objective_summary: 'Screen prime refinance economics, gate to eligible leads, then hand off for review.',
  steps: [
    { step_id: 'step-1', tool: 'fn_build_cohort', params: { states: [] }, rationale: 'Count the broad borrower cohort.' },
    {
      step_id: 'step-2',
      tool: 'fn_segment_counts',
      params: { segment_codes: ['itm', 'listed'], segment_mode: 'any' },
      rationale: 'Keep marketing-eligible, opted-in leads.',
    },
    {
      step_id: 'step-3',
      tool: 'fn_lead_queue_url',
      params: { segment_codes: ['itm'] },
      rationale: 'Prepare the Lead Queue handoff for human review.',
    },
  ],
  expected_outcome: 'An eligible refi and listing subset staged for review.',
  risk_notes: 'The handoff stops for human approval; nothing is sent.',
  requires_approval: true,
};

/** The recompose after a 409: the gate step narrowed and an offer step added. */
export const RECOMPOSED_PLAN: ComposedPlan = {
  ...REVIEWED_PLAN,
  steps: [
    REVIEWED_PLAN.steps[0],
    { ...REVIEWED_PLAN.steps[1], params: { segment_codes: ['itm'], segment_mode: 'any' }, rationale: 'Reworded.' },
    { step_id: 'step-3', tool: 'fn_offer_compare', params: {}, rationale: 'Compare offer fit.' },
    { ...REVIEWED_PLAN.steps[2], step_id: 'step-4' },
  ],
};

export function composeReply(plan: ComposedPlan = REVIEWED_PLAN, digest: string | null = DIGEST_REVIEWED): ComposePlanResponse {
  return {
    status: 'composed',
    planner: 'supervisor_composed',
    model_endpoint: 'mas-growth-supervisor',
    plan,
    plan_digest: digest,
    trace: [],
    approval_required: plan.requires_approval,
    approval_gate_step_id: null,
    executed: false,
    plan_id: null,
    interpreted_intent: `Supervisor composed a ${plan.steps.length}-step plan across ${new Set(plan.steps.map((step) => step.tool)).size} governed tools.`,
    reasoning_summary: 'Every step runs a deterministic tool; state-writing steps stop for human approval.',
    degraded_reason: null,
    message: null,
    fallback_workflows: [],
    audit_event_ids: [],
  };
}

/** The execute 200 for REVIEWED_PLAN: two reads, then the approval gate. */
export function executedReply(digest: string = DIGEST_REVIEWED): ComposePlanResponse {
  return {
    ...composeReply(REVIEWED_PLAN, digest),
    model_endpoint: null,
    interpreted_intent: null,
    reasoning_summary: null,
    executed: true,
    plan_id: 'fixture-plan-0001',
    approval_gate_step_id: 'step-3',
    trace: [
      {
        step_id: 'step-1',
        tool: 'fn_build_cohort',
        label: 'Build borrower cohort',
        status: 'completed',
        detail: `Built the broad borrower cohort: ${TOTALS.inTheMoney.toLocaleString('en-US')} governed rows.`,
        duration_ms: 41,
        row_summary: TOTALS.inTheMoney,
        result_hash: HASH,
        source_asset: BORROWER_360,
        approval_gate: false,
        audit_event_id: 'fixture-audit-plan-step-1',
      },
      {
        step_id: 'step-2',
        tool: 'fn_segment_counts',
        label: 'Apply actionability gates',
        status: 'completed',
        detail: `Applied marketing-eligible, opt-in actionability gates: ${TOTALS.contactableInTheMoney.toLocaleString('en-US')} eligible leads.`,
        duration_ms: 38,
        row_summary: TOTALS.contactableInTheMoney,
        result_hash: HASH,
        source_asset: BORROWER_360,
        approval_gate: false,
        audit_event_id: 'fixture-audit-plan-step-2',
      },
      {
        step_id: 'step-3',
        tool: 'fn_lead_queue_url',
        label: 'Prepare Lead Queue handoff',
        status: 'review_required',
        detail: 'Prepare Lead Queue handoff prepares a reviewed human-review handoff and requires approval. The run stops here; no outreach, activation, or write is executed.',
        duration_ms: 0,
        row_summary: null,
        result_hash: HASH,
        source_asset: EVIDENCE_EVENTS,
        approval_gate: true,
        audit_event_id: null,
      },
    ],
    audit_event_ids: ['fixture-audit-plan-step-1', 'fixture-audit-plan-step-2', 'fixture-audit-plan-compose'],
  };
}

/** The server's 409 body (PLAN_CONFLICT_DETAILS['digest_expired']). */
export const PLAN_CONFLICT = { detail: 'The reviewed plan expired before it was run; compose it again.' };

/** GET /api/growth-agent/runs: the caller's own runs, newest first (genie-09 part 2). */
export const RUNS_LIST: GrowthAgentRunSummary[] = [
  {
    run_id: 'fixture-growth-run-0002',
    workflow_id: 'listing_watch',
    workflow_title: 'Listing watch',
    status: 'completed',
    broad_total: TOTALS.inTheMoney,
    actionable_total: TOTALS.contactableInTheMoney,
    actionable_avg_score: 82.4,
    source_assets: [BORROWER_360],
    audit_event_id: 'fixture-audit-growth-0002',
    created_at: SNAPSHOT_AT,
    monitor_id: 'fixture-monitor-0001',
  },
  {
    run_id: 'fixture-growth-run-0001',
    workflow_id: 'daily_refi_brief',
    workflow_title: 'Daily refi brief',
    status: 'completed',
    broad_total: TOTALS.inTheMoney,
    actionable_total: TOTALS.contactableInTheMoney,
    actionable_avg_score: null,
    source_assets: [BORROWER_360],
    audit_event_id: null,
    created_at: SNAPSHOT_AT,
    monitor_id: null,
  },
];

/** The run Plan reviewed workflow answers with (no watchlist yet, so Save is offered). */
export function plannedRun(): GrowthAgentRunResponse {
  return { ...growthRunFixture(), monitor: null };
}

/** POST /runs/:runId/monitors 200: the watchlist built from the stored run. */
export function saveReply(run: GrowthAgentRunResponse = plannedRun(), cadence: GrowthAgentMonitor['cadence'] = 'daily'): GrowthAgentMonitor {
  return {
    monitor_id: 'fixture-monitor-0001',
    workflow_id: run.workflow.id,
    name: run.workflow.title,
    cadence,
    status: 'active',
    criteria: run.criteria,
    route: run.route,
    actionable_total: run.actionable_total,
    source_assets: run.source_assets,
    last_run_id: run.run_id,
    created_at: SNAPSHOT_AT,
    updated_at: SNAPSHOT_AT,
  };
}

/** The server's 409 when the stored run no longer matches what the card showed. */
export const SAVE_CONFLICT = { detail: 'This run changed or did not complete; run it again before saving.' };

/** GET /monitors/summary: one watchlist with a three-run series. */
export const WATCHLIST_SUMMARY: GrowthAgentWatchlistSummaryResponse = {
  scheduler: { state: 'paused', reason: 'job_schedule' },
  watchlists: [
    {
      monitor_id: 'fixture-monitor-0001',
      workflow_id: 'daily_refi_brief',
      name: 'Daily refi brief',
      cadence: 'daily',
      status: 'active',
      run_count: 3,
      last_run_at: SNAPSHOT_AT,
      previous_run_at: SNAPSHOT_AT,
      actionable_total: TOTALS.contactableInTheMoney,
      previous_actionable_total: TOTALS.contactableInTheMoney - 12,
      actionable_delta: 12,
      actionable_avg_score: 84.6,
      previous_actionable_avg_score: 83.9,
      avg_score_delta: 0.7,
      recent_actionable_totals: [TOTALS.contactableInTheMoney - 20, TOTALS.contactableInTheMoney - 12, TOTALS.contactableInTheMoney],
    },
  ],
};

/** GET /api/growth-agent with an explicit scheduler state (flow-08 slice 2). */
export function homeWithScheduler(state: GrowthAgentSchedulerState, monitors: GrowthAgentMonitor[] = []): GrowthAgentHomeResponse {
  return { ...GROWTH_AGENT_HOME, monitors, scheduler_state: state };
}

export interface RunWatchlistSaveRecorder {
  /** Bodies posted to /agent/run (Plan reviewed workflow). */
  readonly runBodies: GrowthAgentPromptRunRequest[];
  /** [runId, body] for every Save as watchlist post. */
  readonly saves: Array<{ runId: string; body: GrowthAgentRunWatchlistRequest }>;
}

/**
 * Register Plan reviewed workflow (answers plannedRun()) and Save as
 * watchlist (`saved` answers saveReply() and the home read then lists it;
 * `conflict` answers the 409), recording every body.
 */
export function registerRunWatchlistSave(
  mockApi: MockApi,
  outcome: 'saved' | 'conflict',
): RunWatchlistSaveRecorder {
  const runBodies: GrowthAgentPromptRunRequest[] = [];
  const saves: Array<{ runId: string; body: GrowthAgentRunWatchlistRequest }> = [];
  mockApi.register<GrowthAgentRunResponse>('POST', AGENT_RUN_PATTERN, (request) => {
    runBodies.push(request.body as GrowthAgentPromptRunRequest);
    return { body: plannedRun() };
  });
  mockApi.register<GrowthAgentMonitor | typeof SAVE_CONFLICT>('POST', RUN_SAVE_PATTERN, (request) => {
    const body = request.body as GrowthAgentRunWatchlistRequest;
    saves.push({ runId: request.params.runId, body });
    if (outcome === 'conflict') return { status: 409, body: SAVE_CONFLICT };
    return { body: saveReply(plannedRun(), body.cadence ?? 'daily') };
  });
  mockApi.register<GrowthAgentHomeResponse>('GET', HOME_PATTERN, () => ({
    body: homeWithScheduler('paused', saves.length > 0 && outcome === 'saved' ? [saveReply()] : []),
  }));
  return { runBodies, saves };
}

export type ExecuteOutcome = 'ran' | 'conflict';

export interface GrowthAgentTrustRecorder {
  /** Request bodies the browser posted, in order. */
  readonly composeBodies: unknown[];
  readonly executeBodies: ExecutePlanRequest[];
}

/**
 * Register compose (answers `replies` in order, the last one repeating) and
 * execute (`ran` answers executedReply(), `conflict` answers the 409).
 */
export function registerGrowthAgentTrust(
  mockApi: MockApi,
  options: { replies: ComposePlanResponse[]; execute: ExecuteOutcome },
): GrowthAgentTrustRecorder {
  const composeBodies: unknown[] = [];
  const executeBodies: ExecutePlanRequest[] = [];
  mockApi.register<ComposePlanResponse>('POST', COMPOSE_PATTERN, (request) => {
    composeBodies.push(request.body);
    const reply = options.replies[Math.min(composeBodies.length, options.replies.length) - 1];
    return { body: reply };
  });
  mockApi.register<ComposePlanResponse | typeof PLAN_CONFLICT>('POST', EXECUTE_PATTERN, (request) => {
    const body = request.body as ExecutePlanRequest;
    executeBodies.push(body);
    if (options.execute === 'conflict') return { status: 409, body: PLAN_CONFLICT };
    return { body: executedReply(body.plan_digest) };
  });
  return { composeBodies, executeBodies };
}

/** Curated bodies for the fixture contract exporter. */
export function contractSamples(): ContractSample[] {
  const post = (source: string, pattern: string, body: unknown, status = 200): ContractSample => ({
    source,
    method: 'POST',
    pattern,
    path: pattern,
    query: '',
    status,
    body,
  });
  return [
    post('composeReply(REVIEWED_PLAN)', COMPOSE_PATTERN, composeReply()),
    post('composeReply(RECOMPOSED_PLAN)', COMPOSE_PATTERN, composeReply(RECOMPOSED_PLAN, DIGEST_RECOMPOSED)),
    post('composeReply(unsigned)', COMPOSE_PATTERN, composeReply(REVIEWED_PLAN, null)),
    post('executedReply()', EXECUTE_PATTERN, executedReply()),
    post('PLAN_CONFLICT', EXECUTE_PATTERN, PLAN_CONFLICT, 409),
    { source: 'RUNS_LIST', method: 'GET', pattern: RUNS_PATTERN, path: RUNS_PATTERN, query: 'limit=20', status: 200, body: RUNS_LIST },
    { source: 'plannedRun()', method: 'POST', pattern: AGENT_RUN_PATTERN, path: AGENT_RUN_PATTERN, query: '', status: 200, body: plannedRun() },
    { ...post('saveReply()', RUN_SAVE_PATTERN, saveReply()), path: RUN_SAVE_PATTERN.replace(':runId', CONTRACT_RUN_ID) },
    { ...post('SAVE_CONFLICT', RUN_SAVE_PATTERN, SAVE_CONFLICT, 409), path: RUN_SAVE_PATTERN.replace(':runId', CONTRACT_RUN_ID) },
    { source: 'WATCHLIST_SUMMARY', method: 'GET', pattern: WATCHLIST_SUMMARY_PATTERN, path: WATCHLIST_SUMMARY_PATTERN, query: 'limit=20', status: 200, body: WATCHLIST_SUMMARY },
    ...(['active', 'paused', 'unavailable'] as const).map((state): ContractSample => ({
      source: `homeWithScheduler(${state})`,
      method: 'GET',
      pattern: HOME_PATTERN,
      path: HOME_PATTERN,
      query: '',
      status: 200,
      body: homeWithScheduler(state, [saveReply()]),
    })),
  ];
}
