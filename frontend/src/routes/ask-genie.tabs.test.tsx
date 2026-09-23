/**
 * @vitest-environment happy-dom
 *
 * `/ask-genie` page tabs (audit 2026-09-21 `visual-07`, `genie-09`,
 * `flow-10`): the route opens on the conversation, the Growth Agent lives on
 * the Workflows tab and saved watchlists on the Saved monitors tab, the tab
 * is in the URL, and a Growth Agent run shows an honest in-progress state on
 * the tab it was started from.
 */

import { act } from 'react';
import { describe, expect, it } from 'vitest';
import type { GrowthAgentMonitor, GrowthAgentRunResponse } from '../types';
import {
  HOME,
  RUN,
  activePanel,
  button,
  container,
  currentLocation,
  growthAgent,
  mount,
  navigate,
  openTab,
  registerGrowthAgentRoutePanelHooks,
  rerunGrowthAgentMonitor,
  runGrowthAgentWorkflow,
  waitUntil,
} from './ask-genie.growth-agent.test-support';
import { askGeniePanelId, parseAskGenieTab } from './ask-genie.tabs';

const MONITOR: GrowthAgentMonitor = {
  monitor_id: '22222222-2222-4222-8222-222222222222',
  workflow_id: 'daily_refi_brief',
  name: 'Mortgage Growth Agent - IL',
  cadence: 'weekly',
  status: 'active',
  criteria: RUN.criteria,
  route: RUN.route,
  actionable_total: RUN.actionable_total,
  source_assets: RUN.source_assets,
  last_run_id: RUN.run_id,
};

function tabs() {
  return Array.from(container.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
}

function selectedTab() {
  const selected = tabs().filter((tab) => tab.getAttribute('aria-selected') === 'true');
  expect(selected).toHaveLength(1);
  return selected[0];
}

describe('parseAskGenieTab', () => {
  it('reads the two named tabs and falls back to Ask', () => {
    expect(parseAskGenieTab('workflows')).toBe('workflows');
    expect(parseAskGenieTab('monitors')).toBe('monitors');
    expect(parseAskGenieTab(null)).toBe('ask');
    expect(parseAskGenieTab('automate')).toBe('ask');
  });
});

describe('/ask-genie page tabs', () => {
  registerGrowthAgentRoutePanelHooks();

  it('opens on the Ask tab, titled what the nav calls it', async () => {
    mount('/ask-genie');
    await waitUntil(() => container.querySelector('textarea[aria-label="Ask Genie — question"]') !== null);

    expect(container.querySelector('h1')?.textContent).toBe('Ask Genie');
    expect(tabs().map((tab) => tab.textContent)).toEqual(['Ask', 'Workflows', 'Saved monitors']);
    expect(selectedTab().textContent).toBe('Ask');
    expect(selectedTab().tabIndex).toBe(0);
    expect(tabs().filter((tab) => tab.tabIndex === -1)).toHaveLength(2);
    const panel = activePanel();
    expect(panel.getAttribute('aria-labelledby')).toBe(selectedTab().id);
    expect(selectedTab().getAttribute('aria-controls')).toBe(panel.id);
    expect(panel.querySelector('textarea[aria-label="Ask Genie — question"]')).not.toBeNull();
    // The Growth Agent is not on the first fold any more.
    expect(panel.querySelector('textarea[aria-label="Mortgage Growth Agent prompt"]')).toBeNull();
  });

  it('deep-links the Workflows tab and keeps the co-pilot copy there', async () => {
    mount('/ask-genie?tab=workflows');
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    expect(selectedTab().textContent).toBe('Workflows');
    const panel = activePanel();
    expect(panel.querySelector('textarea[aria-label="Mortgage Growth Agent prompt"]')).not.toBeNull();
    expect(panel.querySelector('h2')?.textContent).toBe('Mortgage growth co-pilot');
    expect(container.querySelector('h1')?.textContent).toBe('Ask Genie');
  });

  it('writes the selected tab to the URL, and Ask to the bare path', async () => {
    mount('/ask-genie');
    await waitUntil(() => tabs().length === 3);

    openTab('Workflows');
    expect(currentLocation).toBe('/ask-genie?tab=workflows');
    expect(selectedTab().textContent).toBe('Workflows');
    openTab('Saved monitors');
    expect(currentLocation).toBe('/ask-genie?tab=monitors');
    openTab('Ask');
    expect(currentLocation).toBe('/ask-genie');
  });

  it('moves selection and focus with the arrow, Home and End keys', async () => {
    mount('/ask-genie?tab=workflows');
    await waitUntil(() => tabs().length === 3);
    const press = (key: string) =>
      act(() => {
        (document.activeElement ?? selectedTab()).dispatchEvent(
          new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
        );
      });

    act(() => selectedTab().focus());
    press('End');
    expect(selectedTab().textContent).toBe('Saved monitors');
    expect(document.activeElement).toBe(selectedTab());
    expect(currentLocation).toBe('/ask-genie?tab=monitors');
    press('ArrowRight');
    expect(selectedTab().textContent).toBe('Ask');
    press('ArrowLeft');
    expect(selectedTab().textContent).toBe('Saved monitors');
    press('Home');
    expect(selectedTab().textContent).toBe('Ask');
    expect(currentLocation).toBe('/ask-genie');
    expect(navigate).not.toHaveBeenCalled();
    // Arrowing back to the tab the pass began on returns to that history
    // entry instead of writing another one. `useNavigate` is a spy in this
    // harness; the real Back step is proven in ask-genie-route.fixture.spec.ts.
    press('ArrowRight');
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(-1);
  });

  it('keeps one keyboard pass one entry when keys land before the router renders', async () => {
    mount('/ask-genie');
    await waitUntil(() => tabs().length === 3);
    act(() => selectedTab().focus());
    const press = (key: string) =>
      (document.activeElement ?? selectedTab()).dispatchEvent(
        new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }),
      );

    // One act(): the router renders a new location in a transition, so no
    // key here sees the previous key's tab rendered (a fast typist, or a
    // busy main thread, in the browser).
    act(() => {
      press('ArrowRight');
      press('ArrowRight');
      press('ArrowLeft');
      press('ArrowLeft');
    });
    // Ask → Workflows → Saved monitors → Workflows → Ask: each key moved from
    // the tab it was pressed on, and the last one returned to the pass's
    // origin entry instead of writing another.
    expect(document.activeElement?.textContent).toBe('Ask');
    expect(navigate).toHaveBeenCalledTimes(1);
    expect(navigate).toHaveBeenCalledWith(-1);
  });

  it('shows a run in progress on the Workflows tab, then its result in the same place', async () => {
    let resolveRun: ((value: GrowthAgentRunResponse) => void) | undefined;
    runGrowthAgentWorkflow.mockReturnValueOnce(
      new Promise<GrowthAgentRunResponse>((resolve) => {
        resolveRun = resolve;
      }),
    );
    mount('/ask-genie?tab=workflows');
    await waitUntil(() => container.textContent?.includes('Daily Refi Opportunity Brief') ?? false);

    act(() => button(/^Run$/).click());
    await waitUntil(() => runGrowthAgentWorkflow.mock.calls.length === 1);
    const pending = activePanel().querySelector('[role="status"][aria-label="Growth Agent run in progress"]');
    expect(pending?.textContent).toContain('Running Daily Refi Opportunity Brief');
    expect(pending?.textContent).toContain('appear here when the run finishes');
    // The run slot sits above the workflow cards, not below the custom builder.
    const cards = activePanel().querySelector('.growth-agent__cards');
    expect(pending && cards && pending.compareDocumentPosition(cards) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
    // A busy subtree holds its live-region announcements until it settles,
    // and this card is gone by then: the status must sit outside any
    // aria-busy ancestor. The busy marker stays on the cards the run blocks.
    expect(pending?.closest('[aria-busy="true"]')).toBeNull();
    expect(cards?.getAttribute('aria-busy')).toBe('true');

    await act(async () => {
      resolveRun?.(RUN);
    });
    await waitUntil(() => activePanel().querySelector('[aria-label="Latest Growth Agent run"]') !== null);
    expect(container.querySelector('[aria-label="Growth Agent run in progress"]')).toBeNull();
  });

  it('reports a saved monitor re-run on the Saved monitors tab only', async () => {
    let resolveRerun: ((value: GrowthAgentRunResponse) => void) | undefined;
    growthAgent.mockResolvedValue({ ...HOME, monitors: [MONITOR] });
    rerunGrowthAgentMonitor.mockReturnValueOnce(
      new Promise<GrowthAgentRunResponse>((resolve) => {
        resolveRerun = resolve;
      }),
    );
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes('Mortgage Growth Agent - IL') ?? false);

    act(() => button(/^Run now$/).click());
    await waitUntil(() => rerunGrowthAgentMonitor.mock.calls.length === 1);
    const pending = activePanel().querySelector('[role="status"][aria-label="Growth Agent run in progress"]');
    expect(pending?.textContent).toContain('Re-running Mortgage Growth Agent - IL');
    expect(pending?.closest('[aria-busy="true"]')).toBeNull();

    await act(async () => {
      resolveRerun?.(RUN);
    });
    await waitUntil(() => activePanel().querySelector('[aria-label="Latest Growth Agent run"]') !== null);
    openTab('Workflows');
    expect(activePanel().querySelector('[aria-label="Latest Growth Agent run"]')).toBeNull();
  });

  it('gives the Saved monitors tab an empty state that leads to the workflows', async () => {
    mount('/ask-genie?tab=monitors');
    await waitUntil(() => container.textContent?.includes('No saved monitors yet.') ?? false);

    const openWorkflows = button(/^Open workflows$/);
    openWorkflows.focus();
    act(() => openWorkflows.click());
    expect(currentLocation).toBe('/ask-genie?tab=workflows');
    expect(selectedTab().textContent).toBe('Workflows');
    // The button hid its own panel: focus lands on the selected tab, not <body>.
    expect(document.activeElement).toBe(selectedTab());
  });

  // Scope: copy this route AUTHORS (hero, headings, empty states, captions).
  // Server-sent workflow and monitor text is emptied on purpose, because it
  // is not route copy: the workflow registry's proof points still name
  // columns (e.g. 'borrower_360.in_the_money',
  // backend/services/growth_agent_workflows.py) and the fixture harness's
  // read 'Governed SQL'. Plain lender wording for them is a backend copy
  // follow-up; this lint neither fixes nor vouches for it. Emptying the
  // monitors also leaves out a saved monitor's row, whose hard-coded
  // ' · scheduler paused' status belongs to audit `flow-08` (a capability
  // check replaces it there), so this lint does not vouch for that row either.
  //
  // One mount per case: the harness re-renders ONE root, and a MemoryRouter
  // keeps the location it first mounted with, so a second mount() inside one
  // test would read the first path's panel again. The location and the
  // visible panel are asserted before the text is read, so a case can never
  // quietly lint another tab.
  it.each(['/ask-genie', '/ask-genie?tab=workflows', '/ask-genie?tab=monitors'])(
    'keeps plumbing words out of the route copy on %s (flow-10)',
    async (path) => {
      growthAgent.mockResolvedValue({ ...HOME, workflows: [], monitors: [] });
      mount(path);
      await waitUntil(() => tabs().length === 3 && growthAgent.mock.calls.length > 0);
      await waitUntil(() => !(container.textContent?.includes('Loading workflows…') ?? false));

      expect(currentLocation).toBe(path);
      const expectedTab = parseAskGenieTab(new URLSearchParams(path.split('?')[1] ?? '').get('tab'));
      expect(activePanel().id).toBe(askGeniePanelId(expectedTab));
      expect(selectedTab().getAttribute('aria-controls')).toBe(askGeniePanelId(expectedTab));

      const visible = [container.querySelector('.proto-hero')?.textContent ?? '', activePanel().textContent ?? ''].join(' ');
      for (const banned of [
        /Conversation API/i,
        /Agent Responses/i,
        /capabilit/i,
        /preflight/i,
        /de-duplicat/i,
        /\bsemantics\b/i,
        /hand-editing/i,
        /\bSQL\b/,
        /\bUC\b/,
        /Module 0/,
        /scheduler/i,
      ]) {
        expect(visible, `${path} shows ${banned}`).not.toMatch(banned);
      }
    },
  );
});
