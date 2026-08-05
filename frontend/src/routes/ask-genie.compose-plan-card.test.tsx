/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { ComposePlanResponse, GrowthAgentWorkflow } from '../types';
import { ComposePlanCard } from './ask-genie.compose-plan-card';

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

  function renderResponse(response: ComposePlanResponse) {
    act(() => {
      root.render(<ComposePlanCard response={response} />);
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
});
