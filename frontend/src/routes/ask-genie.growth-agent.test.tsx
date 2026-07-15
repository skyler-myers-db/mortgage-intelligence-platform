/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { describe, expect, it } from 'vitest';
import type { GrowthAgentRunResponse } from '../types';
import {
  HOME,
  RUN,
  button,
  container,
  growthAgent,
  growthAgentCapabilities,
  mount,
  navigate,
  registerGrowthAgentRoutePanelHooks,
  runCustomGrowthAgentWorkflow,
  runGrowthAgentWorkflow,
  runMortgageGrowthAgent,
  setNativeValue,
  stateInput,
  waitUntil,
} from './ask-genie.growth-agent.test-support';

describe('AskGenie Growth Agent route panel', () => {
  registerGrowthAgentRoutePanelHooks();

  it('renders governed workflows and blocks invalid state scopes before posting', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);
    expect(container.textContent).toContain('Mortgage Growth Agent');
    expect(container.textContent).toContain('Borrower Dossier Review');
    act(() => setNativeValue(stateInput(), 'IL illinois'));
    await waitUntil(() => container.textContent?.includes('Invalid: illinois') ?? false);
    expect(button(/^Run$/).disabled).toBe(true);
    expect(runGrowthAgentWorkflow).not.toHaveBeenCalled();
    expect(runCustomGrowthAgentWorkflow).not.toHaveBeenCalled();
    expect(runMortgageGrowthAgent).not.toHaveBeenCalled();
  });

  it('renders governed workflows while live capability readiness is still pending', async () => {
    growthAgent.mockResolvedValueOnce(HOME);
    growthAgentCapabilities.mockImplementationOnce(() => new Promise(() => undefined));
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);
    expect(container.textContent).toContain('Borrower Dossier Review');
    expect(container.textContent).not.toContain('Loading governed workflows');
    expect(growthAgent).toHaveBeenCalledTimes(1);
    expect(growthAgentCapabilities).toHaveBeenCalledTimes(1);
  });

  it('routes a natural-language agent objective through reviewed workflows', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);
    // Platform-capability diagnostics moved to the Admin console; the general-user
    // Ask Genie surface no longer renders the capability panel.
    expect(container.textContent).not.toContain('Platform capabilities');
    expect(container.querySelector('.growth-agent-capability')).toBeNull();

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, 'Find refi and listed borrowers in IL before the branch review.');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => setNativeValue(stateInput(), 'IL'));
    act(() => button(/^Plan reviewed workflow$/).click());

    await waitUntil(() => runMortgageGrowthAgent.mock.calls.length === 1);
    expect(runMortgageGrowthAgent.mock.calls[0][0]).toEqual({
      prompt: 'Find refi and listed borrowers in IL before the branch review.',
      states: ['IL'],
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
    await waitUntil(() => container.textContent?.includes('Structured data lens') ?? false);
    expect(container.textContent).toContain('Reviewed workflow');
    expect(container.textContent).toContain('Run correlation 111111111111');
    expect(container.textContent).toContain('Hash aaaaaaaaaaaa');
    expect(container.textContent).toContain('Audit audit-1111');
    expect(container.textContent).toContain('Masked references only');
    expect(container.textContent).toContain('fn_build_cohort');
    expect(container.textContent).not.toContain('MLflow trace');
  });


  it('does not imply a state scope in the default prompt', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    expect(prompt.value).toContain('current coverage');
    expect(prompt.value).not.toContain('Illinois');

    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => runMortgageGrowthAgent.mock.calls.length === 1);
    expect(runMortgageGrowthAgent.mock.calls[0][0]).toEqual({
      prompt: 'Find prime refinance and listed-for-sale opportunities across current coverage.',
      states: [],
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
  });

  it('renders non-passed governance caveats visibly', async () => {
    runMortgageGrowthAgent.mockResolvedValueOnce({
      ...RUN,
      governance_chips: [
        ...RUN.governance_chips,
        {
          label: 'Preview integration',
          status: 'not_provisioned',
          detail: 'This Databricks preview feature is not provisioned in the workspace.',
          evidence_ref: 'capability:not_provisioned',
        },
        {
          label: 'MLflow trace/eval',
          status: 'not_attached',
          detail: 'No MLflow trace URL or Agent Evaluation result is attached to this run.',
          evidence_ref: null,
        },
      ],
    });
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => container.textContent?.includes('Preview integration') ?? false);

    expect(container.textContent).toContain('Not provisioned');
    expect(container.textContent).toContain('This Databricks preview feature is not provisioned in the workspace.');
    expect(container.textContent).toContain('Not attached');
    expect(container.textContent).toContain('No MLflow trace URL or Agent Evaluation result is attached to this run.');
  });

  it('renders blocked tool and policy states as blocked, not completed or review-only', async () => {
    runMortgageGrowthAgent.mockResolvedValueOnce({
      ...RUN,
      tool_steps: [
        {
          label: 'Approval gate',
          status: 'blocked',
          detail: 'Human approval is required before activation.',
          tool_name: 'fn_lead_queue_url',
          result_hash: 'b'.repeat(64),
        },
      ],
      policy_checks: [
        {
          label: 'Activation policy',
          status: 'blocked',
          detail: 'The agent cannot send outreach automatically.',
        },
      ],
    });
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => container.textContent?.includes('Activation policy') ?? false);

    expect(container.querySelector('.growth-agent-step--blocked')).not.toBeNull();
    expect(container.textContent).toContain('Blocked');
    expect(container.textContent).not.toContain('Approval gatePassed');
  });

  it('runs and opens the reconciled eligible Lead Queue subset', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => setNativeValue(stateInput(), 'il tx il'));
    act(() => button(/^Run$/).click());

    await waitUntil(() => runGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runGrowthAgentWorkflow.mock.calls[0][0]).toBe('daily_refi_brief');
    expect(runGrowthAgentWorkflow.mock.calls[0][1]).toEqual({
      states: ['IL', 'TX'],
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    act(() => button(/Open eligible refi subset/).click());
    expect(navigate).toHaveBeenCalledWith(
      '/lead-queue?segment=itm&marketing_eligibility=Eligible+only&states=IL%2CTX',
    );
  });

  it('clears the prior run card when a later workflow fails validation', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => button(/^Run$/).click());
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    act(() => setNativeValue(stateInput(), 'XX'));
    await waitUntil(() => container.textContent?.includes('Invalid: XX') ?? false);

    expect(container.textContent).not.toContain('117,404');
    expect(container.textContent).not.toContain('5,394');
    expect(runGrowthAgentWorkflow).toHaveBeenCalledTimes(1);
  });

  it('clears the prior run card when valid workflow criteria change', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => setNativeValue(stateInput(), 'IL'));
    act(() => button(/^Run$/).click());
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    act(() => setNativeValue(stateInput(), 'TX'));
    await waitUntil(() => !(container.textContent?.includes('117,404') ?? false));

    act(() => button(/^Run$/).click());
    await waitUntil(() => runGrowthAgentWorkflow.mock.calls.length === 2);
    expect(runGrowthAgentWorkflow.mock.calls[1][1]).toMatchObject({ states: ['TX'] });
  });

  it('clears the custom run card when custom segment logic changes', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => button(/^Run custom$/).click());
    await waitUntil(() => container.textContent?.includes('117,404') ?? false);
    expect(container.textContent).toContain('5,394');

    const mode = container.querySelector<HTMLSelectElement>(
      'select[aria-label="Custom Growth Agent segment logic"]',
    );
    if (!mode) throw new Error('custom mode select not rendered');
    act(() => setNativeValue(mode, 'all'));
    await waitUntil(() => !(container.textContent?.includes('117,404') ?? false));
  });

  it('saves monitors with backend-safe generated labels', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => setNativeValue(stateInput(), 'IL TX'));
    act(() => button(/Save watchlist/).click());

    await waitUntil(() => runGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runGrowthAgentWorkflow.mock.calls[0][1]).toEqual({
      states: ['IL', 'TX'],
      save_monitor: true,
      cadence: 'daily',
      monitor_name: 'Daily Refi Opportunity Brief - IL, TX',
    });
    await waitUntil(() => growthAgent.mock.calls.length >= 2);
  });

  it('runs custom reviewed segment workflows with any mode by default', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => setNativeValue(stateInput(), 'IL TX'));
    act(() => button(/^Run custom$/).click());

    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runCustomGrowthAgentWorkflow.mock.calls[0][0]).toEqual({
      states: ['IL', 'TX'],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'any',
      save_monitor: false,
      cadence: 'daily',
      monitor_name: null,
    });
    await waitUntil(() => container.textContent?.includes('Custom Segment Workflow') ?? false);
    expect(container.textContent).toContain('Reviewed custom workflow');
    expect(container.textContent).toContain('Open eligible custom subset');
  });

  it('saves custom monitors with all mode and selected segment labels', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    const select = container.querySelector<HTMLSelectElement>(
      'select[aria-label="Custom Growth Agent segment logic"]',
    );
    if (!select) throw new Error('custom segment mode select not rendered');
    act(() => setNativeValue(select, 'all'));
    act(() => button(/Save custom watchlist/).click());

    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runCustomGrowthAgentWorkflow.mock.calls[0][0]).toEqual({
      states: [],
      segment_codes: ['itm', 'listed'],
      segment_mode: 'all',
      save_monitor: true,
      cadence: 'daily',
      monitor_name: 'Custom Segment Workflow - ALL - ITM+LISTED',
    });
  });


  it('clears validation errors when the Growth Agent prompt is corrected', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, '  ');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => container.textContent?.includes('Enter a borrower-growth objective') ?? false);

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, 'Find prime refinance opportunities for review.');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });

    expect(container.textContent).not.toContain('Enter a borrower-growth objective');
  });

  it('updates custom segment payloads from chip selection and disables zero-segment runs', async () => {
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => button(/^Listed for Sale$/).click());
    act(() => button(/^Prime Refi Candidates$/).click());
    expect(button(/^Run custom$/).disabled).toBe(true);
    expect(button(/^Save custom watchlist$/).disabled).toBe(true);

    act(() => button(/^Home Equity Candidate$/).click());
    expect(button(/^Run custom$/).disabled).toBe(false);
    act(() => button(/^Run custom$/).click());

    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
    expect(runCustomGrowthAgentWorkflow.mock.calls[0][0]).toMatchObject({
      segment_codes: ['equity'],
      segment_mode: 'any',
      save_monitor: false,
    });
  });

  it('shows a save-specific pending label while saving a custom monitor', async () => {
    let resolveRun: ((value: GrowthAgentRunResponse) => void) | undefined;
    runCustomGrowthAgentWorkflow.mockReturnValueOnce(
      new Promise<GrowthAgentRunResponse>((resolve) => {
        resolveRun = resolve;
      }),
    );
    mount();
    await waitUntil(() => container.textContent?.includes('Build a custom segment workflow') ?? false);

    act(() => button(/^Save custom watchlist$/).click());

    await waitUntil(() => container.textContent?.includes('Saving…') ?? false);
    expect(button(/^Run custom$/).disabled).toBe(true);
    expect(button(/^Saving…$/).disabled).toBe(true);
    expect(container.textContent).not.toContain('Running…');

    await act(async () => {
      resolveRun?.(RUN);
      await Promise.resolve();
    });
    await waitUntil(() => runCustomGrowthAgentWorkflow.mock.calls.length === 1);
  });
});
