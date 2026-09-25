/**
 * @vitest-environment happy-dom
 */

/**
 * Compose, review, Run (audit 2026-09-21 `critic-01`, `genie-09` part 1),
 * through the mounted /ask-genie Workflows tab: Run posts exactly the plan the
 * card shows with its digest and the objective and scope it was composed for,
 * never composes again, renders nothing as run until the server answers, and
 * a 409 runs nothing and sends the lender back to compose.
 */
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../lib/api';
import type { ComposePlanResponse } from '../types/growthAgent';
import {
  button,
  composeMortgageGrowthAgentPlan,
  container,
  executeComposedGrowthAgentPlan,
  mount,
  registerGrowthAgentRoutePanelHooks,
  setNativeValue,
  stateInput,
  waitUntil,
} from './ask-genie.growth-agent.test-support';

registerGrowthAgentRoutePanelHooks();

const OBJECTIVE = 'Compose a refi growth plan for review.';
const DIGEST_A = `v1.v1.1790000000.${'A'.repeat(43)}`;
const DIGEST_B = `v1.v1.1790000100.${'B'.repeat(43)}`;

function composed(overrides: Partial<ComposePlanResponse> = {}): ComposePlanResponse {
  return {
    status: 'composed',
    planner: 'supervisor_composed',
    model_endpoint: 'mas-supervisor-endpoint',
    plan: {
      objective_summary: 'Screen refi economics, then gate to eligible leads.',
      steps: [
        { step_id: 'step-1', tool: 'fn_build_cohort', params: { states: ['IL'] }, rationale: 'Broad screen.' },
        {
          step_id: 'step-2',
          tool: 'fn_segment_counts',
          params: { segment_codes: ['itm'], segment_mode: 'any', states: ['IL'] },
          rationale: 'Gate to eligible leads.',
        },
      ],
      expected_outcome: 'An eligible refi subset.',
      risk_notes: 'Read-only counts.',
      requires_approval: false,
    },
    plan_digest: DIGEST_A,
    trace: [],
    approval_required: false,
    approval_gate_step_id: null,
    executed: false,
    plan_id: null,
    interpreted_intent: 'Supervisor composed a 2-step plan across 2 governed tools.',
    reasoning_summary: 'Every step runs a deterministic tool.',
    degraded_reason: null,
    message: null,
    fallback_workflows: [],
    audit_event_ids: [],
    ...overrides,
  };
}

const RECOMPOSED = composed({
  plan_digest: DIGEST_B,
  plan: {
    ...composed().plan!,
    steps: [
      composed().plan!.steps[0],
      { step_id: 'step-2', tool: 'fn_segment_counts', params: { segment_codes: ['itm', 'listed'], segment_mode: 'all', states: ['IL'] }, rationale: 'Reworded.' },
      { step_id: 'step-3', tool: 'fn_offer_compare', params: {}, rationale: 'Offer fit.' },
    ],
  },
});

function executed(reply: ComposePlanResponse): ComposePlanResponse {
  return {
    ...reply,
    model_endpoint: null,
    interpreted_intent: null,
    reasoning_summary: null,
    executed: true,
    plan_id: 'plan-0001',
    trace: reply.plan!.steps.map((step, index) => ({
      step_id: step.step_id,
      tool: step.tool,
      label: `Step ${index + 1} ran`,
      status: 'completed' as const,
      detail: 'Counted governed rows.',
      duration_ms: 12,
      row_summary: 1200,
      result_hash: 'c'.repeat(64),
      source_asset: 'mip.gold.borrower_360',
      approval_gate: false,
      audit_event_id: `audit-step-${index + 1}`,
    })),
    audit_event_ids: ['audit-step-1', 'audit-step-2', 'audit-compose'],
  };
}

function deferred<T>() {
  let resolve: (value: T) => void = () => undefined;
  let reject: (reason: unknown) => void = () => undefined;
  const promise = new Promise<T>((res, rej) => {
    resolve = res;
    reject = rej;
  });
  return { promise, resolve, reject };
}

function card(): HTMLElement | null {
  return container.querySelector<HTMLElement>('[aria-label="Composed Growth Agent plan"]');
}

function objectiveBox(): HTMLTextAreaElement {
  const box = container.querySelector<HTMLTextAreaElement>('textarea[aria-label="Mortgage Growth Agent prompt"]');
  if (!box) throw new Error('objective box not rendered');
  return box;
}

function typeObjective(value: string) {
  const box = objectiveBox();
  const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
  if (!setter) throw new Error('missing textarea value setter');
  act(() => {
    setter.call(box, value);
    box.dispatchEvent(new Event('input', { bubbles: true }));
  });
}

async function composeFromCommandBar() {
  await waitUntil(() => container.querySelector('textarea[aria-label="Mortgage Growth Agent prompt"]') !== null);
  typeObjective(OBJECTIVE);
  act(() => setNativeValue(stateInput(), 'IL'));
  act(() => button(/^Compose plan$/).click());
  await waitUntil(() => card()?.textContent?.includes('Run this plan') ?? false);
}

function commandBarLabels(): string[] {
  const bar = container.querySelector('[aria-label="Mortgage Growth Agent command center"] .growth-agent-command__actions');
  return Array.from(bar?.querySelectorAll('button') ?? []).map((node) => node.textContent ?? '');
}

describe('Growth Agent compose, review, run', () => {
  it('keeps three command-bar actions, one primary, with a hint that separates the paths', async () => {
    mount();
    await waitUntil(() => commandBarLabels().length > 0);
    expect(commandBarLabels()).toEqual(['Plan reviewed workflow', 'Save reviewed watchlist', 'Compose plan']);
    const bar = container.querySelector('[aria-label="Mortgage Growth Agent command center"]');
    expect(bar?.querySelectorAll('.growth-agent-command__actions .btn--primary')).toHaveLength(1);
    expect(bar?.textContent).toContain(
      'Plan reviewed workflow picks one reviewed workflow and counts eligible borrowers. Compose plan drafts a multi-step plan from reviewed tools; you review each step before anything runs.',
    );
    expect(container.textContent).not.toContain('Execute plan');
  });

  it('Run posts exactly the displayed plan and digest, and never composes again', async () => {
    const reply = composed();
    composeMortgageGrowthAgentPlan.mockResolvedValue(reply);
    const pendingRun = deferred<ComposePlanResponse>();
    executeComposedGrowthAgentPlan.mockReturnValue(pendingRun.promise);
    mount();
    await composeFromCommandBar();

    expect(composeMortgageGrowthAgentPlan).toHaveBeenCalledTimes(1);
    expect(composeMortgageGrowthAgentPlan.mock.calls[0][0]).toEqual({ objective: OBJECTIVE, states: ['IL'] });
    expect(card()?.textContent).toContain('States: IL · Segments: Prime Refi Candidates · Match: any segment');

    act(() => button(/^Run this plan$/).click());
    await waitUntil(() => executeComposedGrowthAgentPlan.mock.calls.length === 1);

    const [request] = executeComposedGrowthAgentPlan.mock.calls[0] as [Record<string, unknown>];
    expect(request).toEqual({ objective: OBJECTIVE, states: ['IL'], plan: reply.plan, plan_digest: DIGEST_A });
    expect(composeMortgageGrowthAgentPlan).toHaveBeenCalledTimes(1);

    // Pessimistic: nothing reads as run until the server answers.
    await waitUntil(() => card()?.textContent?.includes('Running…') ?? false);
    expect(button(/Running…/).disabled).toBe(true);
    expect(card()?.textContent).not.toContain('Execution trace');
    expect(card()?.textContent).toContain('Composed');

    await act(async () => {
      pendingRun.resolve(executed(reply));
      await pendingRun.promise;
    });
    await waitUntil(() => card()?.textContent?.includes('Execution trace') ?? false);
    expect(card()?.textContent).not.toContain('Run this plan');
    expect(card()?.textContent).toContain('Executed');
    // The reviewed plan's own endpoint and reasoning stay on the card.
    expect(card()?.textContent).toContain('mas-supervisor-endpoint');
    expect(card()?.textContent).toContain('Every step runs a deterministic tool.');
    expect(executeComposedGrowthAgentPlan).toHaveBeenCalledTimes(1);
    expect(composeMortgageGrowthAgentPlan).toHaveBeenCalledTimes(1);
  });

  it('a 409 runs nothing, and Compose again brings the current plan to review', async () => {
    composeMortgageGrowthAgentPlan.mockResolvedValueOnce(composed()).mockResolvedValueOnce(RECOMPOSED);
    executeComposedGrowthAgentPlan.mockRejectedValue(
      new ApiError('The reviewed plan expired before it was run; compose it again.', {
        path: '/api/growth-agent/agent/plan/execute',
        status: 409,
      }),
    );
    mount();
    await composeFromCommandBar();

    act(() => button(/^Run this plan$/).click());
    await waitUntil(() => card()?.querySelector('[role="alert"]') !== null);
    expect(card()?.querySelector('[role="alert"]')?.textContent).toContain('This plan was not run.');
    expect(button(/Run this plan/).disabled).toBe(true);
    expect(card()?.textContent).not.toContain('Execution trace');

    act(() => button(/^Compose again$/).click());
    await waitUntil(() => card()?.textContent?.includes('fn_offer_compare') ?? false);
    expect(composeMortgageGrowthAgentPlan).toHaveBeenCalledTimes(2);
    expect(composeMortgageGrowthAgentPlan.mock.calls[1][0]).toEqual({ objective: OBJECTIVE, states: ['IL'] });
    expect(card()?.querySelector('[role="alert"]')).toBeNull();
    expect(button(/^Run this plan$/).disabled).toBe(false);
    expect(executeComposedGrowthAgentPlan).toHaveBeenCalledTimes(1);

    act(() => button(/^Run this plan$/).click());
    await waitUntil(() => executeComposedGrowthAgentPlan.mock.calls.length === 2);
    const [request] = executeComposedGrowthAgentPlan.mock.calls[1] as [Record<string, unknown>];
    expect(request).toEqual({ objective: OBJECTIVE, states: ['IL'], plan: RECOMPOSED.plan, plan_digest: DIGEST_B });
  });

  it('editing the objective clears the card and the next compose is reviewed from scratch', async () => {
    composeMortgageGrowthAgentPlan.mockResolvedValueOnce(composed()).mockResolvedValueOnce(RECOMPOSED);
    mount();
    await composeFromCommandBar();

    typeObjective('Compose a refi and listing growth plan for review.');
    expect(card()).toBeNull();

    act(() => button(/^Compose plan$/).click());
    await waitUntil(() => card()?.textContent?.includes('fn_offer_compare') ?? false);
    expect(composeMortgageGrowthAgentPlan.mock.calls[1][0]).toEqual({
      objective: 'Compose a refi and listing growth plan for review.',
      states: ['IL'],
    });
    expect(executeComposedGrowthAgentPlan).not.toHaveBeenCalled();
  });

  it('a plan the deployment cannot sign is shown but cannot be run', async () => {
    composeMortgageGrowthAgentPlan.mockResolvedValue(composed({ plan_digest: null }));
    mount();
    await composeFromCommandBar();
    expect(button(/^Run this plan$/).disabled).toBe(true);
    expect(card()?.textContent).toContain('this deployment is missing a required security setting');
    act(() => button(/^Run this plan$/).click());
    expect(executeComposedGrowthAgentPlan).not.toHaveBeenCalled();
  });
});
