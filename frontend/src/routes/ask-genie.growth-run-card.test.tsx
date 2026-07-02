/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { GrowthAgentRunResponse } from '../types';
import { GrowthAgentRunCard } from './ask-genie.growth-run-card';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const RUN: GrowthAgentRunResponse = {
  workflow: {
    id: 'daily_refi_brief',
    title: 'Daily Refi Opportunity Brief',
    objective: 'Find borrowers with rate-spread economics worth reviewing today.',
    trigger_label: 'Prime refinance economics',
    action_label: 'Open eligible refi subset',
    source_assets: ['mip.gold.borrower_360'],
    default_route: '/lead-queue?segment=itm&marketing_eligibility=Eligible+only',
    proof_points: ['Broad count uses borrower_360.in_the_money.'],
    cadence_options: ['daily', 'weekly'],
  },
  run_id: '11111111-1111-4111-8111-111111111111',
  specialist_agent: 'structured_data_agent',
  execution_mode: 'agent_framework',
  trace_kind: 'agent_framework',
  planner_label: 'Databricks Supervisor Agent',
  trace_id: 'agent-trace-11111111-1111-4111-8111-111111111111',
  tool_result_hash: 'a'.repeat(64),
  broad_label: 'Broad opportunity',
  actionable_label: 'Eligible subset',
  broad_total: 117404,
  actionable_total: 5394,
  broad_avg_score: 64.2,
  actionable_avg_score: 73.1,
  avg_rate_spread_bps: 187.9,
  avg_equity_pct: 42.4,
  route: '/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL',
  criteria: {
    states: ['IL'],
    lead_queue_filters: {
      segment_codes: ['itm'],
      segment_mode: 'any',
    },
  },
  source_assets: ['mip.gold.borrower_360', 'mip.gold.lead_population'],
  tool_steps: [
    {
      label: 'Supervisor reviewed objective',
      status: 'review_required',
      detail: 'Supervisor selected Listing Watch; deterministic routing selected Daily Refi.',
      tool_name: 'agent_framework_supervisor',
      result_hash: 'c'.repeat(64),
    },
  ],
  policy_checks: [
    {
      label: 'Supervisor workflow selection',
      status: 'review_required',
      detail: 'Supervisor selected Listing Watch while deterministic routing selected Daily Refi.',
    },
  ],
  governance_chips: [
    {
      label: 'Multi-agent framework',
      status: 'review_required',
      detail: 'Supervisor selected a different reviewed workflow; human review is required.',
      evidence_ref: 'agent-trace-11111111-1111-4111-8111-111111111111',
    },
  ],
  interpreted_intent: 'Supervisor Agent selected a reviewed workflow.',
  agent_reasoning: 'The deterministic fallback selected Daily Refi.',
  genie_trusted_assets: ['databricks.supervisor_agent.supervisor-1'],
  audit_event_id: 'audit-11111111-1111-4111-8111-111111111111',
};

const PASSED_RUN: GrowthAgentRunResponse = {
  ...RUN,
  tool_steps: [
    {
      ...RUN.tool_steps[0],
      status: 'completed',
      detail: 'Supervisor and deterministic routing selected Daily Refi.',
    },
  ],
  policy_checks: [
    {
      ...RUN.policy_checks[0],
      status: 'passed',
      detail: 'Supervisor and deterministic routing selected the same workflow.',
    },
  ],
  governance_chips: [
    {
      ...RUN.governance_chips[0],
      status: 'passed',
      detail: 'Supervisor selection matched the deterministic reviewed workflow.',
    },
  ],
};

describe('GrowthAgentRunCard', () => {
  let container: HTMLDivElement;
  let root: Root;
  const onOpenRoute = vi.fn();

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    onOpenRoute.mockReset();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function renderRun(run: GrowthAgentRunResponse) {
    act(() => {
      root.render(
        <GrowthAgentRunCard
          run={run}
          onOpenRoute={onOpenRoute}
          renderSourceAssetChip={(asset) => <span key={asset}>{asset}</span>}
        />,
      );
    });
  }

  it('renders supervisor divergence as a visible review-required state', () => {
    renderRun(RUN);

    const policyCheck = Array.from(
      container.querySelectorAll<HTMLElement>('.growth-agent-policy'),
    ).find((node) => node.textContent?.includes('Supervisor workflow selection'));
    const governanceChip = Array.from(
      container.querySelectorAll<HTMLElement>('.growth-agent-governance-item'),
    ).find((node) => node.textContent?.includes('Multi-agent framework'));

    expect(container.textContent).toContain('Agent framework');
    expect(container.textContent).toContain('Supervisor Agent');
    expect(container.textContent).toContain('Databricks Supervisor Agent');
    expect(container.textContent).toContain('Supervisor reviewed objective');
    expect(container.textContent).toContain('Supervisor workflow selection');
    expect(container.textContent).toContain(
      'Supervisor selected Listing Watch while deterministic routing selected Daily Refi.',
    );
    expect(container.textContent).toContain('Multi-agent framework');
    expect(container.textContent).toContain(
      'Supervisor selected a different reviewed workflow; human review is required.',
    );
    expect(container.querySelector('.growth-agent-step--review_required')).not.toBeNull();
    expect(policyCheck).toBeTruthy();
    expect(policyCheck?.querySelector('.chip--warning')).not.toBeNull();
    expect(policyCheck?.querySelector('.chip--success')).toBeNull();
    expect(governanceChip).toBeTruthy();
    expect(governanceChip?.querySelector('.chip--warning')).not.toBeNull();
    expect(governanceChip?.querySelector('.chip--success')).toBeNull();
  });

  it('renders aligned supervisor selection as passed across policy and governance surfaces', () => {
    renderRun(PASSED_RUN);

    const policyCheck = Array.from(
      container.querySelectorAll<HTMLElement>('.growth-agent-policy'),
    ).find((node) => node.textContent?.includes('Supervisor workflow selection'));
    const governanceChip = Array.from(
      container.querySelectorAll<HTMLElement>('.growth-agent-governance-item'),
    ).find((node) => node.textContent?.includes('Multi-agent framework'));

    expect(container.querySelector('.growth-agent-step--completed')).not.toBeNull();
    expect(container.querySelector('.growth-agent-step--review_required')).toBeNull();
    expect(policyCheck).toBeTruthy();
    expect(policyCheck?.textContent).toContain('Passed');
    expect(policyCheck?.querySelector('.chip--success')).not.toBeNull();
    expect(policyCheck?.querySelector('.chip--warning')).toBeNull();
    expect(governanceChip).toBeTruthy();
    expect(governanceChip?.textContent).toContain('Passed');
    expect(governanceChip?.querySelector('.chip--success')).not.toBeNull();
    expect(governanceChip?.querySelector('.chip--warning')).toBeNull();
  });
});
