/**
 * @vitest-environment happy-dom
 */

/**
 * Saved monitors and a rate watch (audit 2026-09-21 `wow-ai-2`): a rate
 * watch drafts a review note only when its level is reached, so a quiet
 * draft step answers [] and the tab says so in one polite status line,
 * which a draft that did produce notes (or any other watchlist) never shows.
 */
import { act } from 'react';
import { describe, expect, it } from 'vitest';
import type { GrowthAgentMonitor } from '../types/growthAgent';
import {
  HOME,
  RUN,
  button,
  container,
  createGrowthAgentMonitorNotificationDrafts,
  growthAgent,
  mount,
  registerGrowthAgentRoutePanelHooks,
  waitUntil,
} from './ask-genie.growth-agent.test-support';
import { RATE_WATCH_QUIET_MESSAGE } from './ask-genie.growth-agent-run-slot';

registerGrowthAgentRoutePanelHooks();

const RATE_WATCH: GrowthAgentMonitor = {
  monitor_id: '44444444-4444-4444-8444-444444444444',
  workflow_id: 'rate_trigger_watch',
  name: 'Rate watch - 30-year par at or below 5.80 percent',
  cadence: 'daily',
  status: 'active',
  criteria: {},
  route: '/?map_mode=rate&rate_step=-50',
  actionable_total: 0,
  source_assets: [],
  last_run_id: RUN.run_id,
};

function quietLine(): HTMLElement | null {
  return Array.from(container.querySelectorAll<HTMLElement>('[role="status"]')).find(
    (node) => node.textContent === RATE_WATCH_QUIET_MESSAGE,
  ) ?? null;
}

describe('Saved monitors: a rate watch', () => {
  it('says no note was drafted when the level was not reached', async () => {
    growthAgent.mockResolvedValue({ ...HOME, monitors: [RATE_WATCH] });
    createGrowthAgentMonitorNotificationDrafts.mockResolvedValue([]);
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes(RATE_WATCH.name) ?? false);
    expect(quietLine()).toBeNull();

    act(() => button(/^Draft Slack\/Teams$/).click());
    await waitUntil(() => quietLine() !== null);
    expect(quietLine()?.classList.contains('status-callout--info')).toBe(true);
    expect(container.textContent).not.toContain('Watchlist notifications');
  });

  it('shows no quiet line when the watch drafted its notes', async () => {
    growthAgent.mockResolvedValue({ ...HOME, monitors: [RATE_WATCH] });
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes(RATE_WATCH.name) ?? false);
    act(() => button(/^Draft Slack\/Teams$/).click());
    await waitUntil(() => container.textContent?.includes('Watchlist notifications') ?? false);
    expect(quietLine()).toBeNull();
  });
});
