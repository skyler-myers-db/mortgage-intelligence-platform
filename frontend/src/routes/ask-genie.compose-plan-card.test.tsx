/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { ComposedPlan, ComposePlanResponse, GrowthAgentWorkflow } from '../types';
import { ComposePlanCard, type ComposePlanRunControls } from './ask-genie.compose-plan-card';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const FALLBACK_WORKFLOW: GrowthAgentWorkflow = {
  id: 'daily_refi_brief',
  title: 'Daily Refi Opportunity Brief',
  objective: 'Find borrowers with rate-spread economics worth reviewing today.',
  trigger_label: 'Prime refinance economics',
  action_label: 'Open eligible refi subset',
  source_assets: ['mip.gold.borrower_360'],
  default_route: '/lead-queue?segment=itm',
  proof_points: ['Broad count uses borrower_360.in_the_money.'],
  cadence_options: ['daily', 'weekly'],
};

const COMPOSED_EXECUTED: ComposePlanResponse = {
  status: 'composed',
  planner: 'supervisor_composed',
  model_endpoint: 'databricks-meta-llama',
  plan_digest: `v1.v1.1790000000.${'A'.repeat(43)}`,
  plan: {
    objective_summary: 'Surface high-equity HELOC candidates in IL for review.',
    steps: [
      {
        step_id: 'step-1',
        tool: 'query_segment_population',
        params: { segment: 'equity' },
        rationale: 'Pull the reviewed high-equity segment rollup.',
      },
      {
        step_id: 'step-2',
        tool: 'rank_lead_population',
        params: { limit: 50 },
        rationale: 'Rank the eligible subset by opportunity score.',
      },
    ],
    expected_outcome: 'A ranked eligible subset ready for human approval.',
    risk_notes: 'No outreach is sent until a human approves.',
    requires_approval: true,
  },
  trace: [
    {
      step_id: 'step-1',
      tool: 'query_segment_population',
      label: 'Queried segment population',
      status: 'completed',
      detail: 'Returned the reviewed high-equity segment rollup.',
      duration_ms: 812,
      row_summary: 4210,
      result_hash: 'a'.repeat(64),
      source_asset: 'mip.gold.segment_population',
      approval_gate: false,
      audit_event_id: 'audit-step-1',
    },
    {
      step_id: 'step-2',
      tool: 'rank_lead_population',
      label: 'Human approval required before handoff',
      status: 'review_required',
      detail: 'Ranked subset is staged; a human must approve before Lead Queue handoff.',
      duration_ms: 640,
      row_summary: 50,
      result_hash: 'b'.repeat(64),
      source_asset: 'mip.gold.lead_population',
      approval_gate: true,
      audit_event_id: null,
    },
  ],
  approval_required: true,
  approval_gate_step_id: 'step-2',
  executed: true,
  plan_id: 'plan-1111',
  interpreted_intent: 'The objective maps to the high-equity HELOC watch.',
  reasoning_summary: 'Composed a two-step reviewed retrieval plan.',
  degraded_reason: null,
  message: null,
  fallback_workflows: [],
  audit_event_ids: ['audit-step-1'],
};

const DEGRADED: ComposePlanResponse = {
  status: 'degraded',
  planner: 'supervisor_composed',
  model_endpoint: null,
  plan: null,
  plan_digest: null,
  trace: [],
  approval_required: false,
  approval_gate_step_id: null,
  executed: false,
  plan_id: null,
  interpreted_intent: null,
  reasoning_summary: null,
  degraded_reason: 'model_endpoint_unavailable',
  message: 'The planner model is unavailable; use a reviewed catalog workflow instead.',
  fallback_workflows: [FALLBACK_WORKFLOW],
  audit_event_ids: [],
};

const INVALID: ComposePlanResponse = {
  status: 'invalid',
  planner: 'supervisor_composed',
  model_endpoint: 'databricks-meta-llama',
  plan: null,
  plan_digest: null,
  trace: [],
  approval_required: false,
  approval_gate_step_id: null,
  executed: false,
  plan_id: null,
  interpreted_intent: null,
  reasoning_summary: null,
  degraded_reason: null,
  message: 'The objective requested raw borrower identifiers, which is not allowed.',
  fallback_workflows: [],
  audit_event_ids: [],
};

/** A signed plan built from planner-exposed tools. */
const SIGNED_PLAN: ComposedPlan = {
    objective_summary: 'Screen refi economics, then gate to eligible leads.',
    steps: [
      { step_id: 'step-1', tool: 'fn_build_cohort', params: { states: [] }, rationale: 'Broad screen.' },
      {
        step_id: 'step-2',
        tool: 'fn_segment_counts',
        params: { segment_codes: ['itm', 'listed'], segment_mode: 'all', states: ['IL', 'TX'] },
        rationale: 'Gate to eligible, opted-in leads.',
      },
      { step_id: 'step-3', tool: 'fn_borrower_dossier_evidence', params: { min_opportunity_score: 80 }, rationale: 'Evidence.' },
    ],
    expected_outcome: 'An eligible subset.',
    risk_notes: 'Read-only counts.',
    requires_approval: false,
};

/** A signed, composed, not yet executed plan. */
const COMPOSED_SIGNED: ComposePlanResponse = {
  ...INVALID,
  status: 'composed',
  message: null,
  plan_digest: `v1.v1.1790000000.${'B'.repeat(43)}`,
  plan: SIGNED_PLAN,
};

function runControls(overrides: Partial<ComposePlanRunControls> = {}): ComposePlanRunControls {
  return {
    pending: false,
    conflict: false,
    errorMessage: null,
    onRun: vi.fn(),
    onComposeAgain: vi.fn(),
    ...overrides,
  };
}

function runButton(container: HTMLElement): HTMLButtonElement | undefined {
  return Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find((button) =>
    /Run this plan|Running…/.test(button.textContent ?? ''),
  );
}

describe('ComposePlanCard', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderResponse(response: ComposePlanResponse, run?: ComposePlanRunControls) {
    act(() => {
      root.render(<ComposePlanCard response={response} run={run} />);
    });
  }

  it('renders composed+executed plan with steps, trace statuses, approval gate, and honesty label', () => {
    renderResponse(COMPOSED_EXECUTED);

    // Honesty label — must never read as a reviewed catalog workflow.
    expect(container.textContent).toContain('Databricks Agent Responses');
    expect(container.textContent).not.toContain('supervisor_composed');
    expect(container.textContent).toContain('databricks-meta-llama');
    expect(container.textContent).toContain('Model-composed');

    // Objective summary + plan steps.
    expect(container.textContent).toContain('Surface high-equity HELOC candidates in IL for review.');
    expect(container.textContent).toContain('query_segment_population');
    expect(container.textContent).toContain('rank_lead_population');
    expect(container.textContent).toContain('Pull the reviewed high-equity segment rollup.');
    expect(container.textContent).toContain('Requires approval');

    // Execution trace with per-step status chips.
    expect(container.querySelector('.growth-agent-step--completed')).not.toBeNull();
    expect(container.querySelector('.growth-agent-step--review_required')).not.toBeNull();
    expect(container.textContent).toContain('Completed');
    expect(container.textContent).toContain('Review required');

    // Approval gate is visible.
    expect(container.textContent).toContain('Human approval required before execution continues');
    expect(container.textContent).toContain('step-2');
    expect(container.textContent).toContain('Approval gate');
  });

  it('renders a degraded response with the message and fallback workflows and NO plan', () => {
    renderResponse(DEGRADED);

    expect(container.textContent).toContain(
      'The planner model is unavailable; use a reviewed catalog workflow instead.',
    );
    expect(container.textContent).toContain('model_endpoint_unavailable');
    expect(container.textContent).toContain('Reviewed fallback workflows');
    expect(container.textContent).toContain('Daily Refi Opportunity Brief');
    expect(container.textContent).toContain('Reviewed catalog workflow (fallback)');

    // No plan surfaces.
    expect(container.querySelector('.growth-agent-step--completed')).toBeNull();
    expect(container.textContent).not.toContain('Plan steps');
    expect(container.textContent).not.toContain('Requires approval');
  });

  it('renders an invalid response with the message honestly and no plan', () => {
    renderResponse(INVALID);

    expect(container.textContent).toContain(
      'The objective requested raw borrower identifiers, which is not allowed.',
    );
    expect(container.textContent).toContain('Invalid request');
    expect(container.textContent).not.toContain('Plan steps');
    expect(container.textContent).not.toContain('Reviewed fallback workflows');
    expect(container.querySelector('.growth-agent-timeline')).toBeNull();
  });

  it('shows each step its inputs from the closed vocabulary', () => {
    renderResponse(COMPOSED_SIGNED, runControls());
    const metas = Array.from(container.querySelectorAll('.growth-agent-step__meta')).map((node) => node.textContent);
    expect(metas).toContain('States: current coverage');
    expect(metas).toContain('States: IL, TX · Segments: Prime Refi Candidates, Listed for Sale · Match: all segments');
    expect(metas).toContain('Min score: 80');
  });

  it('offers Run only on a composed, signed, unexecuted plan, described by what it does', () => {
    const run = runControls();
    renderResponse(COMPOSED_SIGNED, run);
    const button = runButton(container);
    expect(button?.textContent).toBe('Run this plan');
    expect(button?.disabled).toBe(false);
    expect(button?.classList.contains('btn--primary')).toBe(true);
    const hint = document.getElementById(button?.getAttribute('aria-describedby') ?? '');
    expect(hint?.textContent).toBe(
      'Runs exactly the 3 steps above with the inputs shown. Read-only: it counts and checks, stops at the first approval gate and sends nothing.',
    );
    act(() => button?.click());
    expect(run.onRun).toHaveBeenCalledTimes(1);

    for (const other of [COMPOSED_EXECUTED, DEGRADED, INVALID]) {
      renderResponse(other, runControls());
      expect(runButton(container), other.status).toBeUndefined();
    }
  });

  it('disables Run with the reason when the deployment cannot sign the plan', () => {
    renderResponse({ ...COMPOSED_SIGNED, plan_digest: null }, runControls());
    const button = runButton(container);
    expect(button?.disabled).toBe(true);
    expect(document.getElementById(button?.getAttribute('aria-describedby') ?? '')?.textContent).toBe(
      'This plan can be reviewed here but not run: this deployment is missing a required security setting. Ask an administrator.',
    );
  });

  it('holds the result until the server answers: pending is Running…, disabled and announced', () => {
    renderResponse(COMPOSED_SIGNED, runControls({ pending: true }));
    const button = runButton(container);
    expect(button?.textContent).toBe('Running…');
    expect(button?.disabled).toBe(true);
    expect(container.querySelector('[role="status"]')?.textContent).toBe('Running the plan you reviewed.');
    expect(container.textContent).not.toContain('Execution trace');
  });

  it('says a 409 ran nothing and offers Compose again', () => {
    const run = runControls({ conflict: true });
    renderResponse(COMPOSED_SIGNED, run);
    const alert = container.querySelector('.status-callout--danger[role="alert"]');
    expect(alert?.textContent).toBe(
      'This plan was not run. It changed, expired or no longer passes review since it was composed. Compose it again to review the current plan.',
    );
    expect(runButton(container)?.disabled).toBe(true);
    const again = Array.from(container.querySelectorAll('button')).find((node) => node.textContent === 'Compose again');
    expect(again?.classList.contains('btn--ghost')).toBe(true);
    act(() => again?.click());
    expect(run.onComposeAgain).toHaveBeenCalledTimes(1);
    expect(run.onRun).not.toHaveBeenCalled();
  });

  it('shows any other failure in an alert and keeps Run available', () => {
    renderResponse(COMPOSED_SIGNED, runControls({ errorMessage: 'lakebase is temporarily unavailable' }));
    expect(container.querySelector('[role="alert"]')?.textContent).toBe('lakebase is temporarily unavailable');
    expect(runButton(container)?.disabled).toBe(false);
    expect(container.textContent).not.toContain('Compose again');
  });
});
