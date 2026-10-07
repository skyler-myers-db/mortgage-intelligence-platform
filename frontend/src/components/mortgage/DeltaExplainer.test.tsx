/**
 * @vitest-environment happy-dom
 */
/**
 * DeltaExplainer (audit wow-ai-3): where one "since your last login" number
 * moved, in the evidence drawer. The bars and the table reconcile with the
 * whole-book change (the unattributed remainder included), the nearest
 * snapshot and the population are disclosed, coincident facts never read as
 * causes, and nothing paints in an alarm hue.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { QueryClientProvider } from '@tanstack/react-query';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the colocated stylesheet under Vitest only.
import { readFileSync } from 'node:fs';
import { createMipQueryClient } from '../../lib/queryClient';
import type { HomeSummaryAttributionResponse } from '../../types/homeAttribution';
import DeltaExplainer from './DeltaExplainer';
import { OTHER_STATES_LABEL, UNATTRIBUTED_LABEL, waterfallSteps, withinAttributionWindow } from './deltaExplainer.model';

declare const process: { cwd(): string };
(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const fetchMock = vi.fn();
/** The tests' today: the baselines below sit inside (or, on purpose, outside) the route's lookback. */
const TODAY = Date.parse('2026-10-01T12:00:00Z');

const STATES = [
  ['TX', 400, 520], ['FL', 310, 360], ['IL', 300, 280], ['CA', 500, 530], ['AZ', 90, 100], ['GA', 70, 75], ['OH', 60, 62], ['WA', 40, 41],
] as const;

function body(overrides: Partial<HomeSummaryAttributionResponse> = {}): HomeSummaryAttributionResponse {
  const states = STATES.map(([state, then, now]) => ({ state, baseline_count: then, current_count: now, change: now - then }))
    .sort((a, b) => Math.abs(b.change) - Math.abs(a.change) || a.state.localeCompare(b.state));
  const attributed = states.reduce((sum, row) => sum + row.change, 0);
  return {
    measure: 'refi_economics_screen',
    label: 'refi candidates',
    population: 'addressable',
    requested_baseline_date: '2026-09-01',
    baseline_snapshot_date: '2026-09-01',
    current_snapshot_date: '2026-09-30',
    nearest_snapshot: false,
    baseline_total: 2_000,
    current_total: 2_000 + attributed + 12,
    total_change: attributed + 12,
    states,
    unattributed_change: 12,
    rate: { series_id: 'MORTGAGE30US', baseline_week: '2026-08-31', baseline_pct: 6.41, latest_week: '2026-09-28', latest_pct: 6.22 },
    offer_rules_last_updated: '2026-09-15T10:00:00',
    offer_rules_changed_since_baseline: true,
    sources: ['mip.gold.funnel_snapshot_daily', 'mip.gold.rate_window_weekly', 'mip.ref.offer_rules_config'],
    note: 'These coincided with the change; they are not shown as causes.',
    ...overrides,
  };
}

function respond(payload: unknown, headers: Record<string, string> = {}): void {
  fetchMock.mockImplementation(async () =>
    new Response(JSON.stringify(payload), { status: 200, headers: { 'Content-Type': 'application/json', ...headers } }));
}

describe('DeltaExplainer', () => {
  let root: Root;

  beforeEach(() => {
    vi.useFakeTimers({ toFake: ['Date'] });
    vi.setSystemTime(TODAY);
    vi.stubGlobal('fetch', fetchMock);
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    fetchMock.mockReset();
    vi.unstubAllGlobals();
    vi.useRealTimers();
  });

  async function render(baselineDate = '2026-09-01'): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={createMipQueryClient()}>
          <MemoryRouter>
            <DeltaExplainer explainer={{ measure: 'refi_economics_screen', baselineDate, liveDisplay: '+2,250' }} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 60 && !document.querySelector('[data-testid="delta-explainer-total"], .delta-explainer .body'); i += 1) {
      await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
    }
  }

  const text = () => (document.querySelector('[data-testid="delta-explainer"]')?.textContent ?? '').replace(/\s+/g, ' ');

  it('reads the attribution once, for this measure and baseline', async () => {
    respond(body());
    await render();
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(String(fetchMock.mock.calls[0][0])).toBe(
      '/api/v1/home/summary/attribution?measure=refi_economics_screen&baseline=2026-09-01',
    );
  });

  it('draws the baseline, six states, the other states, the unattributed remainder and now, reconciling to the total', async () => {
    const response = body();
    respond(response);
    await render();
    const kinds = [...document.querySelectorAll('[data-testid="delta-explainer-waterfall"] g')].map((g) => g.getAttribute('data-kind'));
    expect(kinds).toEqual(['total', 'state', 'state', 'state', 'state', 'state', 'state', 'other', 'unattributed', 'total']);
    expect(document.querySelector('[data-testid="delta-explainer-waterfall"]')?.getAttribute('aria-hidden')).toBe('true');

    const steps = waterfallSteps(response) ?? [];
    expect(steps[steps.length - 2].to).toBe(response.current_total);
    const rows = [...document.querySelectorAll('.delta-explainer__table tbody tr')];
    const labels = rows.map((row) => row.querySelector('th')?.textContent);
    expect(labels).toEqual(['TX', 'FL', 'CA', 'IL', 'AZ', 'GA', OTHER_STATES_LABEL, UNATTRIBUTED_LABEL]);
    const changes = rows.map((row) => Number((row.querySelectorAll('td')[2]?.textContent ?? '').replace(/[+,]/g, '')));
    expect(changes.reduce((sum, value) => sum + value, 0)).toBe(response.total_change);
    expect(document.querySelector('[data-testid="delta-explainer-total"]')?.textContent).toBe(`+${response.total_change}`);
    // Each state opens its cohort in the Lead Queue: the measure's predicate plus the state.
    expect(rows[0].querySelector('a')?.getAttribute('href')).toBe('/lead-queue?segment=itm&state=TX');
    expect(rows[6].querySelector('a')).toBeNull();
  });

  it('discloses the population, the coincident facts as non-causes, and the live-figure reconciliation', async () => {
    respond(body());
    await render();
    expect(text()).toContain('Addressable borrowers (the whole book); the Lead Queue shows the contactable subset.');
    expect(text()).toContain('Coincided with');
    expect(text()).toContain('30-year par 6.41% (week of Aug 31), now 6.22% (week of Sep 28)');
    expect(text()).toContain('Offer rules last changed Sep 15');
    expect(text()).toContain('These coincided with the change; they are not shown as causes.');
    expect(text()).toContain('The live figure +2,250 compares the live metric view with the KPI snapshot nearest your visit');
    expect(text()).not.toMatch(/\bcaused\b|\bbecause\b/i);
    expect(document.querySelector('[data-testid="delta-explainer-nearest"]')).toBeNull();
  });

  it('says when the nearest snapshot stands in for the baseline, and when the rules did not change', async () => {
    respond(body({ nearest_snapshot: true, baseline_snapshot_date: '2026-08-29', offer_rules_changed_since_baseline: false }));
    await render();
    expect(document.querySelector('[data-testid="delta-explainer-nearest"]')?.textContent?.replace(/\s+/g, ' ')).toBe(
      'No snapshot was taken on Sep 1; the nearest one (Aug 29) is used.',
    );
    expect(text()).toContain('No offer-rules change since your baseline');
  });

  it('is honest when no snapshot covers the period: no bars, no invented change', async () => {
    respond(body({ baseline_total: null, current_total: null, total_change: null, states: [], unattributed_change: null, baseline_snapshot_date: null, current_snapshot_date: null }));
    await render();
    for (let i = 0; i < 40 && !text().includes('No daily funnel snapshot'); i += 1) {
      await act(async () => new Promise((resolve) => setTimeout(resolve, 5)));
    }
    expect(text()).toContain('No daily funnel snapshot covers this period yet');
    expect(document.querySelector('[data-testid="delta-explainer-waterfall"]')).toBeNull();
  });

  it('marks a retained serve with its last good read', async () => {
    respond(body(), { 'X-Data-Last-Good-At': '2026-09-30T08:00:00Z' });
    await render();
    expect(document.querySelector('[data-testid="stale-data-note"] time')?.getAttribute('dateTime')).toBe('2026-09-30T08:00:00.000Z');
  });

  it("never asks for a baseline older than the route's 400-day lookback (a 422), and says why", async () => {
    respond(body());
    await render('2025-08-26'); // 401 days before TODAY
    expect(fetchMock, 'no attribution read the route would refuse').not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="delta-explainer-too-old"]')?.textContent?.replace(/\s+/g, ' ')).toBe(
      'The change is broken down by state for a baseline from the last 400 days; your baseline (Aug 26, 2025) is older.',
    );
    expect(text()).not.toContain('could not load');
    expect(document.querySelector('[data-testid="delta-explainer-waterfall"]')).toBeNull();
  });

  it('asks on the last day the route still answers, counting whole UTC days as the route does', async () => {
    expect(withinAttributionWindow('2026-10-01', Date.parse('2026-10-01T23:59:00Z'))).toBe(true);
    expect(withinAttributionWindow('2025-08-27', Date.parse('2026-10-01T23:59:00Z'))).toBe(true); // 400 days
    expect(withinAttributionWindow('2025-08-26', Date.parse('2026-10-01T00:01:00Z'))).toBe(false); // 401 days
    expect(withinAttributionWindow('not-a-date', TODAY)).toBe(false);
    respond(body({ requested_baseline_date: '2025-08-27' }));
    await render('2025-08-27');
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(document.querySelector('[data-testid="delta-explainer-too-old"]')).toBeNull();
  });

  it('paints increases in the data ink and decreases in the tertiary ink, never an alarm hue', async () => {
    respond(body());
    await render();
    const bars = [...document.querySelectorAll('.delta-explainer__bar')].map((bar) => bar.getAttribute('class'));
    expect(bars).toContain('delta-explainer__bar delta-explainer__bar--up');
    expect(bars).toContain('delta-explainer__bar delta-explainer__bar--down');
    const css = readFileSync(`${process.cwd()}/src/components/mortgage/DeltaExplainer.css`, 'utf8') as string;
    expect(css).toMatch(/\.delta-explainer__bar--up \{ fill: var\(--accent-data\); \}/);
    expect(css).toMatch(/\.delta-explainer__bar--down \{ fill: var\(--text-3\); \}/);
    expect(css).not.toContain('signal-danger');
    expect(document.querySelector('[data-testid="delta-explainer"]')?.innerHTML).not.toContain('signal-danger');
  });

  it("gives the row headers and the total the .tbl td cell box, not the user agent's centred bold", async () => {
    respond(body());
    await render();
    // Every body row and the total are headed by a <th scope="row">, which .tbl leaves unstyled.
    const rows = [...document.querySelectorAll('.delta-explainer__table tbody tr, .delta-explainer__table tfoot tr')];
    expect(rows.map((row) => row.firstElementChild?.tagName)).toEqual(rows.map(() => 'TH'));
    const declarations = (block: string) => block.split(';').map((part) => part.trim()).filter(Boolean);
    const tblCss = readFileSync(`${process.cwd()}/src/design-system/components/03-score-and-table.css`, 'utf8') as string;
    const tdBox = declarations(tblCss.match(/\n\.tbl td \{([^}]*)\}/)?.[1] ?? '')
      .filter((declaration) => /^(padding|height|border-bottom|vertical-align):/.test(declaration));
    expect(tdBox).toHaveLength(4);
    const css = readFileSync(`${process.cwd()}/src/components/mortgage/DeltaExplainer.css`, 'utf8') as string;
    const th = declarations(css.match(/\.delta-explainer__table tbody th,\s*\.delta-explainer__table tfoot th \{([^}]*)\}/)?.[1] ?? '');
    expect(th).toEqual(expect.arrayContaining([...tdBox, 'text-align: start', 'font-weight: 500', 'color: var(--text-1)']));
    const total = declarations(css.match(/\.delta-explainer__table tfoot th,\s*\.delta-explainer__table tfoot td \{([^}]*)\}/)?.[1] ?? '');
    expect(total).toContain('font-weight: 600');
  });
});
