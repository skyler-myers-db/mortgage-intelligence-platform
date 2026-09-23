/**
 * Fixtures for the `/ask-genie` route lane (audit 2026-09-21 `visual-07`,
 * `genie-09`, `flow-10`).
 *
 * Nothing here is a default read, so nothing is added to registry.ts: a spec
 * that runs a Growth Agent workflow registers the run itself with
 * `registerHeldWorkflowRun`, which holds the POST until the test releases it
 * (the pending state is what the test asserts).
 *
 * Synthetic only: counts come from the shared footprint (reference.ts), the
 * run returns counts and route filters, no borrower ids, names or contact
 * fields. The workflow is the registry's own `daily_refi_brief`.
 */
import type { GrowthAgentRunResponse } from '../../../../src/types';
import type { MockApi } from '../mockApi';
import { GROWTH_AGENT_HOME } from './genie';
import { SNAPSHOT_AT, TOTALS } from './reference';

const DAILY_REFI = GROWTH_AGENT_HOME.workflows[0];
const RUN_ID = 'fixture-growth-run-0001';
const TRACE_ID = `agent-trace-${RUN_ID}`;
const RESULT_HASH = 'b'.repeat(64);

export const GROWTH_RUN_PATTERN = '/api/growth-agent/workflows/:workflowId/run';

/** A completed reviewed run of the fixture's Daily refi brief workflow. */
export function growthRunFixture(): GrowthAgentRunResponse {
  return {
    workflow: DAILY_REFI,
    run_id: RUN_ID,
    specialist_agent: 'structured_data_agent',
    execution_mode: 'deterministic',
    trace_kind: 'local_hash',
    planner_label: 'Reviewed workflow runner',
    trace_id: TRACE_ID,
    tool_result_hash: RESULT_HASH,
    broad_label: 'Broad opportunity',
    actionable_label: 'Eligible subset',
    broad_total: TOTALS.inTheMoney,
    actionable_total: TOTALS.contactableInTheMoney,
    broad_avg_score: 81.2,
    actionable_avg_score: 84.6,
    avg_rate_spread_bps: 112,
    avg_equity_pct: 46.1,
    route: '/lead-queue?segment=itm&marketing_eligibility=Eligible+only',
    criteria: { states: [], lead_queue_filters: { segment_codes: ['itm'], segment_mode: 'any' } },
    source_assets: ['mip.gold.borrower_360'],
    tool_steps: [
      {
        label: 'Read reviewed borrower signals',
        status: 'completed',
        detail: `Found ${TOTALS.inTheMoney.toLocaleString('en-US')} borrowers in the broad opportunity screen.`,
        source_asset: 'mip.gold.borrower_360',
        tool_name: 'fn_build_cohort',
        result_hash: RESULT_HASH,
      },
    ],
    policy_checks: [
      {
        label: 'Broad vs actionable reconciliation',
        status: 'passed',
        detail: `${TOTALS.inTheMoney.toLocaleString('en-US')} broad opportunities reconcile to ${TOTALS.contactableInTheMoney.toLocaleString('en-US')} eligible leads.`,
      },
    ],
    governance_chips: [
      {
        label: 'Human approval required',
        status: 'passed',
        detail: 'The run returns counts and route filters only.',
        evidence_ref: TRACE_ID,
      },
    ],
    interpreted_intent: 'Reviewed workflow runner selected the daily refi brief.',
    agent_reasoning: 'No model-generated SQL: the reviewed workflow ran its own query.',
    genie_trusted_assets: [],
    audit_event_id: 'fixture-audit-growth-0001',
    created_at: SNAPSHOT_AT,
  };
}

export interface HeldWorkflowRun {
  /** POSTs the browser has sent to the run endpoint. */
  readonly posts: number;
  /** Let the held run return its result. */
  release(): void;
}

/** Register the workflow-run POST, held open until `release()`. */
export function registerHeldWorkflowRun(mockApi: MockApi): HeldWorkflowRun {
  let open: () => void = () => undefined;
  const gate = new Promise<void>((resolve) => {
    open = resolve;
  });
  let posts = 0;
  mockApi.register<GrowthAgentRunResponse>('POST', GROWTH_RUN_PATTERN, async () => {
    posts += 1;
    await gate;
    return { body: growthRunFixture() };
  });
  return {
    get posts() {
      return posts;
    },
    release() {
      open();
    },
  };
}
