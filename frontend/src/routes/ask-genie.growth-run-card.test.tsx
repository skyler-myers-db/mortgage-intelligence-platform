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
  actionable_cohort_fingerprint: 'b'.repeat(64),
  actionable_snapshot_id: '2026-07-14 12:00:00',
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
    {
      label: 'AI Gateway',
      status: 'review_required',
      detail:
        'Supervisor call was routed through the configured AI Gateway endpoint; deployment-level exact inference-row proof is required before AI Gateway is claimable. This run card does not claim per-run row landing.',
      evidence_ref: null,
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
    const gatewayChip = Array.from(
      container.querySelectorAll<HTMLElement>('.growth-agent-governance-item'),
    ).find((node) => node.textContent?.includes('AI Gateway'));

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
    expect(container.textContent).toContain('AI Gateway');
    expect(container.textContent).toContain(
      'Supervisor call was routed through the configured AI Gateway endpoint',
    );
    expect(container.textContent).toContain('does not claim per-run row landing');
    expect(container.textContent).not.toContain('mip-agent-run-');
    expect(container.querySelector('.growth-agent-step--review_required')).not.toBeNull();
    expect(policyCheck).toBeTruthy();
    expect(policyCheck?.querySelector('.chip--warning')).not.toBeNull();
    expect(policyCheck?.querySelector('.chip--success')).toBeNull();
    expect(governanceChip).toBeTruthy();
    expect(governanceChip?.querySelector('.chip--warning')).not.toBeNull();
    expect(governanceChip?.querySelector('.chip--success')).toBeNull();
    expect(gatewayChip).toBeTruthy();
    expect(gatewayChip?.querySelector('.chip--warning')).not.toBeNull();
    expect(gatewayChip?.querySelector('.chip--success')).toBeNull();
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

  it('carries the complete cohort proof into the Lead Queue action', () => {
    renderRun(PASSED_RUN);

    const open = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes('Open eligible refi subset'),
    );
    if (!open) throw new Error('Growth Agent action not rendered');
    act(() => open.click());

    const route = onOpenRoute.mock.calls[0][0] as string;
    const url = new URL(route, 'https://mortgage-intelligence.local');
    expect(url.pathname).toBe('/lead-queue');
    expect(url.searchParams.get('growth_agent_run_id')).toBe(PASSED_RUN.run_id);
    expect(url.searchParams.get('actionable_total')).toBe('5394');
    expect(url.searchParams.get('tool_result_hash')).toBe(PASSED_RUN.tool_result_hash);
    expect(url.searchParams.get('actionable_cohort_fingerprint'))
      .toBe(PASSED_RUN.actionable_cohort_fingerprint);
    expect(url.searchParams.get('actionable_snapshot_id')).toBe(PASSED_RUN.actionable_snapshot_id);
    expect(container.textContent).toContain('Cohort proof bbbbbbbbbbbb');
    expect(container.textContent).toContain('Snapshot 2026-07-14 12:00:00');
  });

  it('preserves the generic route when a non-proof response is replayed', () => {
    const genericRun = {
      ...PASSED_RUN,
      actionable_cohort_fingerprint: null,
      actionable_snapshot_id: null,
    };
    renderRun(genericRun);

    const open = Array.from(container.querySelectorAll<HTMLButtonElement>('button')).find(
      (button) => button.textContent?.includes('Open eligible refi subset'),
    );
    if (!open) throw new Error('Growth Agent action not rendered');
    act(() => open.click());

    expect(onOpenRoute).toHaveBeenCalledWith(genericRun.route);
    expect(container.textContent).toContain('Cohort proof unavailable');
  });
});
