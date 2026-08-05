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
  createGrowthAgentMonitorNotificationDrafts,
  growthAgent,
  mount,
  navigate,
  registerGrowthAgentRoutePanelHooks,
  rerunGrowthAgentMonitor,
  runMortgageGrowthAgent,
  setNativeValue,
  stateInput,
  waitUntil,
} from './ask-genie.growth-agent.test-support';

describe('AskGenie Growth Agent saved watchlists', () => {
  registerGrowthAgentRoutePanelHooks();

  it('saves natural-language monitors with reviewed filters only', async () => {
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
      .mockResolvedValueOnce(HOME)
      .mockResolvedValueOnce({ ...HOME, monitors: [savedMonitor] });
    runMortgageGrowthAgent.mockResolvedValueOnce({
      ...RUN,
      monitor: savedMonitor,
    });
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
    act(() => button(/^Save reviewed watchlist$/).click());

    await waitUntil(() => runMortgageGrowthAgent.mock.calls.length === 1);
    expect(runMortgageGrowthAgent.mock.calls[0][0]).toEqual({
      prompt: 'Find refinance opportunities for branch follow-up.',
      states: ['IL'],
      save_monitor: true,
      cadence: 'weekly',
      monitor_name: 'Mortgage Growth Agent - IL',
    });
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);
    expect(container.textContent).toContain('Saved watchlists');
    expect(container.textContent).toContain('5,394');
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
    mount();
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Run now$/).click());

    await waitUntil(() => rerunGrowthAgentMonitor.mock.calls.length === 1);
    expect(rerunGrowthAgentMonitor.mock.calls[0]).toEqual([
      '22222222-2222-4222-8222-222222222222',
      {},
    ]);
    await waitUntil(() => container.textContent?.includes('Saved watchlist runner') ?? false);
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
    mount();
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
    mount();
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
    mount();
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Run now$/).click());
    await waitUntil(() => rerunGrowthAgentMonitor.mock.calls.length === 1);

    expect(button(/^Plan reviewed workflow$/).disabled).toBe(true);
    expect(button(/^Run$/).disabled).toBe(true);
    expect(button(/^Run custom$/).disabled).toBe(true);
    expect(button(/^Open$/).disabled).toBe(false);

    await act(async () => {
      resolveRerun?.(RUN);
    });
    await waitUntil(() => button(/^Run$/).disabled === false);
  });
});
