/**
 * @vitest-environment happy-dom
 */
/**
 * WatchlistBriefings (audit 2026-09-21 wow-ai-4): Home's read-only card over
 * the audit-free GET /api/growth-agent/monitors/summary. It says honestly
 * whether scheduled runs are on, lists up to four briefings with their
 * run-over-run change, and never POSTs: loading Home never starts a run.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { createMipQueryClient } from '../../lib/queryClient';
import type { GrowthAgentWatchlistBriefing, GrowthAgentWatchlistSummaryResponse } from '../../types/growthAgent';
import WatchlistBriefings, { WATCHLIST_BRIEFING_ROWS } from './WatchlistBriefings';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fetchMock = vi.fn();

function briefing(index: number, overrides: Partial<GrowthAgentWatchlistBriefing> = {}): GrowthAgentWatchlistBriefing {
  return {
    monitor_id: `monitor-${index}`,
    workflow_id: 'daily_refi_brief',
    name: `Watchlist ${index}`,
    cadence: 'daily',
    status: 'active',
    run_count: 3,
    last_run_at: '2026-10-01T06:00:00Z',
    previous_run_at: '2026-09-30T06:00:00Z',
    actionable_total: 52,
    previous_actionable_total: 44,
    actionable_delta: 8,
    actionable_avg_score: 74.6,
    previous_actionable_avg_score: 72.3,
    avg_score_delta: 2.3,
    recent_actionable_totals: [40, 44, 52],
    ...overrides,
  };
}

function summary(
  watchlists: GrowthAgentWatchlistBriefing[],
  state: GrowthAgentWatchlistSummaryResponse['scheduler']['state'] = 'paused',
): GrowthAgentWatchlistSummaryResponse {
  return { scheduler: { state, reason: state === 'unavailable' ? 'not_configured' : 'job_schedule' }, watchlists };
}

function respond(body: unknown, status = 200): void {
  fetchMock.mockImplementation(async () =>
    new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } }));
}

describe('WatchlistBriefings', () => {
  let root: Root;

  beforeEach(() => {
    vi.stubGlobal('fetch', fetchMock);
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    fetchMock.mockReset();
    vi.unstubAllGlobals();
  });

  async function render(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={createMipQueryClient()}>
          <MemoryRouter>
            <WatchlistBriefings />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 100 && fetchMock.mock.calls.length === 0; i += 1) {
      await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
    }
    for (let i = 0; i < 20; i += 1) await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
  }

  const rows = () => [...document.querySelectorAll('[data-testid="watchlist-briefing"]')];
  const text = (el: Element | undefined) => (el?.textContent ?? '').replace(/\s+/g, ' ').trim();

  it('reads only GET /growth-agent/monitors/summary, once, and never POSTs', async () => {
    respond(summary([briefing(1)]));
    await render();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    const [url, init] = fetchMock.mock.calls[0] as [string, RequestInit | undefined];
    expect(url).toBe('/api/v1/growth-agent/monitors/summary');
    expect((init?.method ?? 'GET').toUpperCase()).toBe('GET');
  });

  it('says honestly that scheduled runs are off, in a warning chip', async () => {
    respond(summary([briefing(1)], 'paused'));
    await render();
    const chip = document.querySelector('.watchlist-briefings__scheduler .chip');
    expect(chip?.textContent).toBe('scheduled runs off');
    expect(chip?.classList.contains('chip--warning')).toBe(true);
    expect(document.querySelector('.surface__hdr h2')?.textContent).toBe('Watchlist briefings');
  });

  it('shows an active scheduler as a neutral chip', async () => {
    respond(summary([briefing(1)], 'active'));
    await render();
    const chip = document.querySelector('.watchlist-briefings__scheduler .chip');
    expect(chip?.textContent).toBe('scheduled runs on');
    expect(chip?.classList.contains('chip--neutral')).toBe(true);
  });

  it('lists the actionable count, the change since the last run, the score change, the age and a sparkline text', async () => {
    respond(summary([briefing(1)]));
    await render();
    const row = rows()[0];
    expect(row.querySelector('a')?.getAttribute('href')).toBe('/ask-genie?tab=workflows');
    expect(text(row)).toContain('52 actionable');
    expect(text(row)).toContain('+8 since last run');
    expect(text(row)).toContain('avg score +2.3');
    expect(row.querySelector('svg.spark')?.getAttribute('aria-hidden')).toBe('true');
    expect(text(row.querySelector('.sr-only') ?? undefined)).toBe('Recent runs: 40, 44, 52 actionable.');
    expect(row.querySelector('time')?.getAttribute('dateTime')).toBe('2026-10-01T06:00:00.000Z');
  });

  it("a first run says so, with no score change and no invented delta", async () => {
    respond(summary([
      briefing(1, {
        status: 'paused', run_count: 1, previous_run_at: null, previous_actionable_total: null, actionable_delta: null,
        previous_actionable_avg_score: null, avg_score_delta: null, recent_actionable_totals: [52],
      }),
      briefing(2, { last_run_at: null, run_count: 0, actionable_total: null, recent_actionable_totals: [] }),
    ]));
    await render();
    const [first, never] = rows();
    expect(text(first)).toContain('first run');
    expect(text(first)).toContain('avg score —');
    expect(first.querySelector('.chip')?.textContent).toBe('Paused');
    expect(first.querySelector('svg.spark')).toBeNull();
    expect(text(never)).toContain('Not run yet');
    expect(text(never)).toContain('— actionable');
  });

  it(`lists at most ${WATCHLIST_BRIEFING_ROWS} and links the rest`, async () => {
    respond(summary(Array.from({ length: 6 }, (_, index) => briefing(index + 1))));
    await render();
    expect(rows()).toHaveLength(WATCHLIST_BRIEFING_ROWS);
    const more = document.querySelector('.watchlist-briefings__more');
    expect(more?.textContent).toBe('+2 more watchlists');
    expect(more?.getAttribute('href')).toBe('/ask-genie?tab=workflows');
  });

  it('says how to start when there is no watchlist yet', async () => {
    respond(summary([]));
    await render();
    expect(rows()).toHaveLength(0);
    const empty = document.querySelector('.watchlist-briefings__empty');
    expect(text(empty ?? undefined)).toBe('No saved watchlists yet. Save a Growth Agent run as a watchlist in Ask Genie.');
    expect(empty?.querySelector('a')?.getAttribute('href')).toBe('/ask-genie?tab=workflows');
  });

  it('a failed read speaks the shared vocabulary for "Watchlist briefings", never the server text', async () => {
    respond({ detail: 'SENTINEL boom' }, 500);
    await render();
    expect(rows()).toHaveLength(0);
    expect(document.body.textContent).toContain('Watchlist briefings');
    expect(document.body.textContent).not.toContain('SENTINEL');
  });
});
