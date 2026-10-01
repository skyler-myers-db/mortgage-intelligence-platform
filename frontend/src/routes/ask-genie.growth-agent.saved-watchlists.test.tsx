/**
 * @vitest-environment happy-dom
 */

import { act } from 'react';
import { describe, expect, it } from 'vitest';
import { ApiError } from '../lib/api';
import type { GrowthAgentRunResponse } from '../types';
import {
  HOME,
  RUN,
  activePanel,
  button,
  container,
  createGrowthAgentMonitorNotificationDrafts,
  growthAgent,
  mount,
  navigate,
  openTab,
  registerGrowthAgentRoutePanelHooks,
  rerunGrowthAgentMonitor,
  runMortgageGrowthAgent,
  saveGrowthAgentRunWatchlist,
  setNativeValue,
  stateInput,
  waitUntil,
} from './ask-genie.growth-agent.test-support';

/**
 * What POST /agent/run answers when no reviewed workflow matches the objective
 * (backend growth_agent_live_analysis.py): a read-only Genie analysis with no
 * run-ledger row, a 32-hex hash and the policy check "No state written". The
 * server's workflow Literal includes 'live_analysis'; the hand-written TS union
 * does not, hence the cast.
 */
const LIVE_ANALYSIS_RUN = {
  ...RUN,
  workflow: {
    ...RUN.workflow,
    id: 'live_analysis',
    title: 'Live analysis',
    action_label: 'Review the governed analysis; no state was written.',
    default_route: '/ask-genie',
  },
  run_id: '33333333-3333-4333-8333-333333333333',
  monitor: null,
  execution_mode: 'genie_conversation',
  trace_kind: 'genie_conversation',
  planner_label: 'Live Genie analysis (read-only fallback)',
  tool_result_hash: 'c'.repeat(32),
  broad_total: 12,
  actionable_total: 0,
  route: '/ask-genie',
  criteria: { prompt: 'Find refinance opportunities for branch follow-up.' },
  policy_checks: [
    {
      label: 'No state written',
      status: 'passed',
      detail: 'Read-only analysis; campaigns, monitors, and Lead Queue were not modified.',
    },
  ],
  audit_event_id: null,
} as unknown as GrowthAgentRunResponse;

function saveStatus(): HTMLElement | null {
  return activePanel().querySelector<HTMLElement>('.growth-agent-run__save [role="status"]');
}

describe('AskGenie Growth Agent saved watchlists', () => {
  registerGrowthAgentRoutePanelHooks();

  it('saves exactly the run shown from its card: one plan, no second /agent/run', async () => {
    const savedMonitor = {
      monitor_id: '22222222-2222-4222-8222-222222222222',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Daily Refi Opportunity Brief - IL',
      cadence: 'weekly' as const,
      status: 'active' as const,
      criteria: RUN.criteria,
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent
      .mockResolvedValueOnce(HOME)
      .mockResolvedValueOnce({ ...HOME, monitors: [savedMonitor] });
    saveGrowthAgentRunWatchlist.mockResolvedValueOnce(savedMonitor);
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);

    const prompt = container.querySelector<HTMLTextAreaElement>(
      'textarea[aria-label="Mortgage Growth Agent prompt"]',
    );
    if (!prompt) throw new Error('agent prompt not rendered');
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')?.set;
      setter?.call(prompt, 'Find refinance opportunities for branch follow-up.');
      prompt.dispatchEvent(new Event('input', { bubbles: true }));
      prompt.dispatchEvent(new Event('change', { bubbles: true }));
    });
    act(() => setNativeValue(stateInput(), 'IL'));
    const cadence = container.querySelector<HTMLSelectElement>(
      'select[aria-label="Growth Agent review interval"]',
    );
    if (!cadence) throw new Error('review cadence select not rendered');
    act(() => setNativeValue(cadence, 'weekly'));
    act(() => button(/^Plan reviewed workflow$/).click());

    await waitUntil(() => runMortgageGrowthAgent.mock.calls.length === 1);
    // Planning never saves: the watchlist comes from the run card, from exactly this run.
    expect(runMortgageGrowthAgent.mock.calls[0][0]).toEqual({
      prompt: 'Find refinance opportunities for branch follow-up.',
      states: ['IL'],
      save_monitor: false,
      cadence: 'weekly',
      monitor_name: null,
    });
    await waitUntil(() => activePanel().querySelector('[aria-label="Latest Growth Agent run"]') !== null);
    const save = button(/^Save as watchlist$/);
    const hint = document.getElementById(save.getAttribute('aria-describedby') ?? '');
    expect(hint?.textContent).toBe(
      "Saves this run's reviewed workflow and filters as a watchlist with the Weekly review interval. Nothing runs again and nothing is sent.",
    );
    act(() => save.click());

    await waitUntil(() => saveGrowthAgentRunWatchlist.mock.calls.length === 1);
    expect(saveGrowthAgentRunWatchlist.mock.calls[0].slice(0, 2)).toEqual([
      RUN.run_id,
      { tool_result_hash: RUN.tool_result_hash, cadence: 'weekly' },
    ]);
    await waitUntil(() => saveStatus() !== null);
    expect(saveStatus()?.textContent).toBe(
      'Saved as watchlist “Daily Refi Opportunity Brief - IL”. Find it under Saved monitors.',
    );
    // The pressed button is gone; focus lands on its confirmation, not on <body>.
    expect(document.activeElement).toBe(saveStatus());
    expect(activePanel().textContent).not.toContain('Save as watchlist');
    expect(runMortgageGrowthAgent).toHaveBeenCalledTimes(1);
    await waitUntil(() => growthAgent.mock.calls.length >= 2);
    openTab('Saved monitors');
    await waitUntil(() => activePanel().textContent?.includes('Daily Refi Opportunity Brief - IL') ?? false);
    expect(activePanel().textContent).toContain('Saved watchlists');
    expect(activePanel().textContent).toContain('5,394');
  });

  it('says a run that changed cannot be saved as shown, and keeps nothing', async () => {
    saveGrowthAgentRunWatchlist.mockRejectedValueOnce(
      new ApiError('This run changed or did not complete; run it again before saving.', {
        path: `/api/growth-agent/runs/${RUN.run_id}/monitors`,
        status: 409,
      }),
    );
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);
    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => activePanel().querySelector('[aria-label="Latest Growth Agent run"]') !== null);
    act(() => button(/^Save as watchlist$/).click());
    await waitUntil(() => activePanel().querySelector('.growth-agent-run__save [role="alert"]') !== null);
    expect(activePanel().querySelector('.growth-agent-run__save [role="alert"]')?.textContent).toBe(
      "This run can't be saved as shown. Run it again, then save.",
    );
    expect(growthAgent).toHaveBeenCalledTimes(1);
    expect(runMortgageGrowthAgent).toHaveBeenCalledTimes(1);
  });

  it('offers no Save as watchlist on a read-only live-analysis answer', async () => {
    runMortgageGrowthAgent.mockResolvedValueOnce(LIVE_ANALYSIS_RUN);
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);
    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => activePanel().querySelector('[aria-label="Latest Growth Agent run"]') !== null);
    const card = activePanel().querySelector('[aria-label="Latest Growth Agent run"]');
    expect(card?.textContent).toContain('Live analysis');
    expect(card?.textContent).toContain('No state written');
    // No ledger row backs it, so the save route could only refuse it: nothing is offered.
    expect(card?.textContent).not.toContain('Save as watchlist');
    expect(card?.querySelector('.growth-agent-run__save')).toBeNull();
    expect(saveGrowthAgentRunWatchlist).not.toHaveBeenCalled();
  });

  it('shows a 422 as the refused sentence, never the validation text', async () => {
    saveGrowthAgentRunWatchlist.mockRejectedValueOnce(
      new ApiError("tool_result_hash: String should match pattern '^[0-9a-f]{64}$'", {
        path: `/api/growth-agent/runs/${RUN.run_id}/monitors`,
        status: 422,
        validationIssues: [
          { field: 'tool_result_hash', message: "String should match pattern '^[0-9a-f]{64}$'", location: ['body', 'tool_result_hash'] },
        ],
      }),
    );
    mount();
    await waitUntil(() => container.textContent?.includes('Growth objective') ?? false);
    act(() => button(/^Plan reviewed workflow$/).click());
    await waitUntil(() => activePanel().querySelector('[aria-label="Latest Growth Agent run"]') !== null);
    act(() => button(/^Save as watchlist$/).click());
    await waitUntil(() => activePanel().querySelector('.growth-agent-run__save [role="alert"]') !== null);
    expect(activePanel().querySelector('.growth-agent-run__save [role="alert"]')?.textContent).toBe(
      "This run can't be saved as shown. Run it again, then save.",
    );
    expect(container.textContent).not.toContain('tool_result_hash');
    expect(container.textContent).not.toContain('should match pattern');
  });

  it('re-runs saved watchlists without replaying raw prompt text', async () => {
    const savedMonitor = {
      monitor_id: '22222222-2222-4222-8222-222222222222',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Mortgage Growth Agent - IL',
      cadence: 'weekly' as const,
      status: 'active' as const,
      criteria: {
        states: ['IL'],
        lead_queue_filters: {
          segment_codes: ['itm'],
          segment_mode: 'any',
        },
      },
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent
      .mockResolvedValueOnce({ ...HOME, monitors: [savedMonitor] })
      .mockResolvedValueOnce({ ...HOME, monitors: [savedMonitor] });
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Run now$/).click());

    await waitUntil(() => rerunGrowthAgentMonitor.mock.calls.length === 1);
    expect(rerunGrowthAgentMonitor.mock.calls[0]).toEqual([
      '22222222-2222-4222-8222-222222222222',
      {},
    ]);
    await waitUntil(() => container.textContent?.includes('Saved watchlist runner') ?? false);
    // The re-run reports on the tab it was started from (audit genie-09).
    expect(activePanel().querySelector('[aria-label="Latest Growth Agent run"]')).not.toBeNull();
    expect(container.textContent).toContain('Saved watchlist re-run: Mortgage Growth Agent - IL.');
    expect(container.textContent).toContain('Eligible subset');
    expect(container.textContent).not.toContain('run this for John Smith');

    act(() => button(/Open eligible refi subset/).click());
    expect(navigate).toHaveBeenCalledWith(RUN.route);
  });

  it('renders distinct Slack alerts and Teams operations briefs from saved watchlists', async () => {
    const savedMonitor = {
      monitor_id: '22222222-2222-4222-8222-222222222222',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Mortgage Growth Agent - IL',
      cadence: 'weekly' as const,
      status: 'active' as const,
      criteria: RUN.criteria,
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent.mockResolvedValue({ ...HOME, monitors: [savedMonitor] });
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Draft Slack\/Teams$/).click());

    await waitUntil(() => createGrowthAgentMonitorNotificationDrafts.mock.calls.length === 1);
    expect(createGrowthAgentMonitorNotificationDrafts.mock.calls[0]).toEqual([
      '22222222-2222-4222-8222-222222222222',
      { channels: ['slack', 'teams'] },
    ]);
    await waitUntil(() => container.textContent?.includes('Slack alert') ?? false);
    expect(container.textContent).toContain('Watchlist notifications');
    expect(container.textContent).toContain('Databricks Agent Responses');
    expect(container.textContent).not.toContain('Supervisor-composed notification');
    expect(container.textContent).toContain('Governed notification framework');
    expect(container.textContent).toContain('Slack alert');
    expect(container.textContent).toContain('5,394 eligible borrowers in Mortgage Growth Agent - IL.');
    expect(container.textContent).toContain('Teams operations brief');
    expect(container.textContent).toContain('Operator action: Review the current watchlist');
    expect(container.querySelector('.growth-agent-card__copy--structured')?.textContent).toContain('Operations brief\nWatchlist:');
    expect(container.textContent).toContain('Status: draft');
    expect(container.textContent).not.toContain('No borrower identities');
    expect(container.textContent).not.toContain('Not sent');
  });

  it('shows inactive saved watchlists but blocks reruns', async () => {
    const pausedMonitor = {
      monitor_id: '33333333-3333-4333-8333-333333333333',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Paused refi watchlist',
      cadence: 'daily' as const,
      status: 'paused' as const,
      criteria: RUN.criteria,
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent.mockResolvedValue({ ...HOME, monitors: [pausedMonitor] });
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes('Paused refi watchlist') ?? false);

    expect(container.textContent).toContain('Paused');
    const runButton = button(/^Run now$/);
    expect(runButton.disabled).toBe(true);
    act(() => runButton.click());
    expect(rerunGrowthAgentMonitor).not.toHaveBeenCalled();
    expect(button(/^Draft Slack\/Teams$/).disabled).toBe(true);
    act(() => button(/^Open$/).click());
    expect(navigate).toHaveBeenCalledWith(RUN.route);
  });


  it('locks other Growth Agent actions while a saved monitor rerun is pending', async () => {
    let resolveRerun: ((value: GrowthAgentRunResponse) => void) | undefined;
    const savedMonitor = {
      monitor_id: '22222222-2222-4222-8222-222222222222',
      workflow_id: 'daily_refi_brief' as const,
      name: 'Mortgage Growth Agent - IL',
      cadence: 'weekly' as const,
      status: 'active' as const,
      criteria: RUN.criteria,
      route: RUN.route,
      actionable_total: RUN.actionable_total,
      source_assets: RUN.source_assets,
      last_run_id: RUN.run_id,
    };
    growthAgent.mockResolvedValue({ ...HOME, monitors: [savedMonitor] });
    rerunGrowthAgentMonitor.mockReturnValueOnce(
      new Promise<GrowthAgentRunResponse>((resolve) => {
        resolveRerun = resolve;
      }),
    );
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Run now$/).click());
    await waitUntil(() => rerunGrowthAgentMonitor.mock.calls.length === 1);
    expect(button(/^Open$/).disabled).toBe(false);

    openTab('Workflows');
    expect(button(/^Plan reviewed workflow$/).disabled).toBe(true);
    expect(button(/^Run$/).disabled).toBe(true);
    expect(button(/^Run custom$/).disabled).toBe(true);

    await act(async () => {
      resolveRerun?.(RUN);
    });
    await waitUntil(() => button(/^Run$/).disabled === false);
  });
});
