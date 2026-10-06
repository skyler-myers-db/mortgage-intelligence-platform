/**
 * @vitest-environment happy-dom
 *
 * "Crossed the line" at the rendered layer (audit wow-stage-4). The traps:
 * GET /borrowers/{id} writes a VIEW_BORROWER row, so the chart may only READ
 * the cached dossier, never fetch it; and the audit-free rate-window read may
 * fire only for an eligible borrower while the drawer is open.
 */
import { QueryClient, QueryClientProvider, useQuery } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// test reads the colocated stylesheet under Vitest only.
import { readFileSync } from 'node:fs';
import { queryKeys } from '../../lib/queryKeys';
import type { Borrower360, RateWindowResponse } from '../../types';
import { SpreadHistoryChart } from './SpreadHistoryChart';

declare const process: { cwd(): string };

const apiMocks = vi.hoisted(() => ({
  borrower: vi.fn(),
  analyticsRateWindow: vi.fn(),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    borrower: apiMocks.borrower,
    analyticsRateWindow: apiMocks.analyticsRateWindow,
  },
}));

const BORROWER_ID = 'B-SPREADTEST01';

const RATE_WINDOW: RateWindowResponse = {
  series_id: 'MORTGAGE30US',
  weeks: [
    { week: '2025-01-06', market_rate_pct: 7.2, itm_count: 0 },
    { week: '2025-01-13', market_rate_pct: 6.9, itm_count: 0 },
    { week: '2025-01-20', market_rate_pct: 6.5, itm_count: 0 },
    { week: '2025-01-27', market_rate_pct: 6.13, itm_count: 0 },
    { week: '2025-02-03', market_rate_pct: 6.1, itm_count: 0 },
    { week: '2025-02-10', market_rate_pct: 6.05, itm_count: 0, is_latest: true },
  ],
  book_lien_count: 10,
  book_as_of: '2025-02-12T00:00:00Z',
  thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
  provenance: {
    market_rate_source: 'mip.silver.market_rates_weekly',
    book_source: 'mip.gold.borrower_360',
    gold_source: 'mip.gold.rate_window_weekly',
    rule_source: 'mip.gold.fn_rate_spread',
    note: 'test',
  },
};

function dossier(overrides: Partial<Borrower360> = {}): Borrower360 {
  return {
    borrower_id: BORROWER_ID,
    display_name: 'Owner SPREAD01',
    city: 'Austin',
    state: 'TX',
    zip: '78701',
    clip: 'clip_demo_spread',
    segment_codes: ['itm'],
    equity_estimate: 200000,
    rate_spread_bps: 82,
    opportunity_score: 80,
    confidence: 70,
    recommended_offer: 'Refinance',
    why_now: 'Rate spread clears the screen.',
    evidence_ids: [],
    approval_status: 'pending',
    clip_id: 'clip_demo_spread',
    owner_link_id: 'ol_demo_spread',
    subject_property: 'Synthetic property · Austin, TX 78701',
    avm_value: 500000,
    current_lien_balance: 300000,
    current_rate: 6.875,
    ltv: 60,
    related_property_count: 1,
    trigger_timeline: [],
    evidence_events: [],
    why_panel: {
      rate_spread_bps: 82,
      market_rate: 0.0605,
      equity_pct: 40,
      in_the_money: true,
      in_the_money_reason: 'ok',
      min_spread_bps: 75,
      min_equity_pct: 15,
      sources: [],
    },
    first_pos_date: '2019-04-15',
    first_pos_rate_type: 'FIX',
    first_itm_week: '2025-02-03',
    ...overrides,
  };
}

async function settle(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

function text(): string {
  return document.body.textContent ?? '';
}

function button(name: string): HTMLButtonElement {
  const found = Array.from(document.querySelectorAll('button')).find((item) => item.textContent?.includes(name));
  if (!(found instanceof HTMLButtonElement)) throw new Error(`Button not found: ${name}`);
  return found;
}

describe('SpreadHistoryChart', () => {
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    document.body.innerHTML = '<div id="root"></div>';
    root = createRoot(document.getElementById('root') as HTMLElement);
    client = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    apiMocks.analyticsRateWindow.mockResolvedValue(RATE_WINDOW);
    apiMocks.borrower.mockResolvedValue(dossier());
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    vi.clearAllMocks();
    document.body.innerHTML = '';
  });

  async function render(active = true, node = <SpreadHistoryChart borrowerId={BORROWER_ID} active={active} />) {
    await act(async () => {
      root.render(<QueryClientProvider client={client}>{node}</QueryClientProvider>);
    });
    await settle();
  }

  it('an uncached dossier shows the open-the-dossier note and reads nothing', async () => {
    await render();
    expect(text()).toContain('Open the borrower dossier to see the rate history.');
    expect(apiMocks.borrower).not.toHaveBeenCalled();
    expect(apiMocks.analyticsRateWindow).not.toHaveBeenCalled();
  });

  it.each([
    ['ARM', { first_pos_rate_type: 'ARM' as const }],
    ['an unknown rate type', { first_pos_rate_type: null }],
  ])('%s shows the FIX-only state with no series read', async (_label, overrides) => {
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier(overrides));
    await render();
    expect(text()).toContain('Rate history is drawn for fixed-rate first liens only.');
    expect(apiMocks.analyticsRateWindow).not.toHaveBeenCalled();
    expect(apiMocks.borrower).not.toHaveBeenCalled();
  });

  it('a zero note rate says there is nothing to compare, with no series read', async () => {
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier({ current_rate: 0 }));
    await render();
    expect(text()).toContain('No active fixed note rate to compare.');
    expect(apiMocks.analyticsRateWindow).not.toHaveBeenCalled();
  });

  it('a closed drawer never reads the series, even for an eligible borrower', async () => {
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier());
    await render(false);
    expect(apiMocks.analyticsRateWindow).not.toHaveBeenCalled();
    expect(document.querySelector('[data-testid="spread-history-loading"]')).not.toBeNull();
  });

  it('an eligible borrower reads the shared series once and draws the server crossing', async () => {
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier());
    await render();
    expect(apiMocks.analyticsRateWindow).toHaveBeenCalledTimes(1);
    expect(apiMocks.borrower).not.toHaveBeenCalled();
    expect(client.getQueryData(queryKeys.analytics('rate-window'))).toEqual(RATE_WINDOW);
    expect(document.querySelector('[data-testid="spread-history-caption"]')?.textContent).toBe(
      'In the money since the week of Feb 3, 2025',
    );
    expect(text()).toContain("Today's rule and today's equity applied to past rates. History, not a forecast.");
    const svg = document.querySelector('[data-testid="spread-history-svg"]');
    expect(svg?.getAttribute('aria-hidden')).toBe('true');
    expect(svg?.querySelector('title')).toBeNull();
    expect(document.querySelector('.spread-history [title]')).toBeNull();
    expect(svg?.querySelector('.spread-history__market')?.getAttribute('pathLength')).toBe('1');
    expect(svg?.querySelector('[data-testid="spread-history-crossing"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="spread-history-summary"]')?.textContent).toContain(
      'Note rate 6.88% against the weekly 30-year fixed rate',
    );
    expect(text()).not.toMatch(/forecast(?!\.)|will be|expected to/i);
  });

  it('a left-censored run and a not-in-the-money borrower get their own captions', async () => {
    client.setQueryData(
      queryKeys.borrower(BORROWER_ID),
      dossier({ first_itm_week: '2025-01-06', first_pos_date: '2012-03-01', current_rate: 9 }),
    );
    await render();
    expect(document.querySelector('[data-testid="spread-history-caption"]')?.textContent).toBe(
      'In the money since at least the week of Jan 6, 2025, when the rate series starts',
    );
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier({ first_itm_week: null }));
    await render();
    expect(document.querySelector('[data-testid="spread-history-caption"]')?.textContent).toBe(
      'Not in the money at the latest 30-year rate',
    );
    expect(document.querySelector('[data-testid="spread-history-crossing"]')).toBeNull();
  });

  it('the table toggle reveals a Week / 30-year rate table with the crossing row marked', async () => {
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier());
    await render();
    const toggle = button('Show as table');
    const controls = toggle.getAttribute('aria-controls');
    expect(toggle.getAttribute('aria-expanded')).toBe('false');
    expect(document.getElementById(controls as string)?.hidden).toBe(true);

    await act(async () => toggle.click());

    expect(toggle.getAttribute('aria-expanded')).toBe('true');
    expect(toggle.textContent).toBe('Hide table');
    const region = document.getElementById(controls as string);
    expect(region?.hidden).toBe(false);
    const headers = Array.from(region?.querySelectorAll('th') ?? []).map((th) => th.textContent);
    expect(headers).toEqual(['Week', '30-year rate']);
    const rows = region?.querySelectorAll('tbody tr') ?? [];
    expect(rows).toHaveLength(6);
    const crossing = region?.querySelector('tr[data-crossing]');
    expect(crossing?.textContent).toContain('Feb 3, 2025');
    expect(crossing?.querySelector('.chip')?.textContent).toBe('Crossed');
    expect(crossing?.textContent).toContain('in the money from here');
    expect(region?.querySelectorAll('tr[data-crossing]')).toHaveLength(1);
  });

  it('a failed series read shows the warning with an explicit retry and never re-reads on its own', async () => {
    apiMocks.analyticsRateWindow.mockRejectedValueOnce(new Error('boom'));
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier());
    await render();
    const error = document.querySelector('[data-testid="spread-history-error"]');
    expect(error?.textContent).toContain('Rate history unavailable');
    expect(button('Try again').disabled).toBe(false);
    await settle();
    expect(apiMocks.analyticsRateWindow, 'no automatic re-read after a failure').toHaveBeenCalledTimes(1);

    let release: (value: RateWindowResponse) => void = () => undefined;
    apiMocks.analyticsRateWindow.mockImplementationOnce(() => new Promise((resolve) => { release = resolve; }));
    await act(async () => button('Try again').click());
    await settle();
    expect(apiMocks.analyticsRateWindow).toHaveBeenCalledTimes(2);
    // While the retry is in flight the skeleton replaces the warning, so the
    // button cannot send a second read.
    expect(document.querySelector('[data-testid="spread-history-loading"]')).not.toBeNull();
    expect(document.querySelector('[data-testid="spread-history-error"]')).toBeNull();
    await act(async () => release(RATE_WINDOW));
    await settle();
    expect(document.querySelector('[data-testid="spread-history-caption"]')).not.toBeNull();
  });

  it('shows a skeleton while the series loads', async () => {
    apiMocks.analyticsRateWindow.mockImplementationOnce(() => new Promise(() => undefined));
    client.setQueryData(queryKeys.borrower(BORROWER_ID), dossier());
    await render();
    const loading = document.querySelector('[data-testid="spread-history-loading"]');
    expect(loading?.getAttribute('aria-busy')).toBe('true');
    expect(loading?.querySelector('.skeleton, [class*="skeleton"]')).not.toBeNull();
  });

  it('never breaks the dossier refetch an approve triggers while the chart is mounted', async () => {
    function DossierObserver() {
      useQuery({ queryKey: queryKeys.borrower(BORROWER_ID), queryFn: () => apiMocks.borrower(BORROWER_ID) });
      return null;
    }
    await render(true, (
      <>
        <DossierObserver />
        <SpreadHistoryChart borrowerId={BORROWER_ID} active />
      </>
    ));
    expect(apiMocks.borrower).toHaveBeenCalledTimes(1);
    // Re-render the chart last, so its observer is the last to set options.
    await render(true, (
      <>
        <DossierObserver />
        <SpreadHistoryChart borrowerId={BORROWER_ID} active={false} />
      </>
    ));
    await act(async () => {
      await client.invalidateQueries({ queryKey: ['mip', 'borrower'] });
    });
    await settle();
    expect(apiMocks.borrower).toHaveBeenCalledTimes(2);
    expect(client.getQueryState(queryKeys.borrower(BORROWER_ID))?.status).toBe('success');
  });

  it('animates only under prefers-reduced-motion: no-preference, with --dur tokens', () => {
    const css = readFileSync(`${process.cwd()}/src/components/mortgage/SpreadHistoryChart.css`, 'utf8') as string;
    const motion = css.match(/@media \(prefers-reduced-motion: no-preference\) \{([\s\S]*?)\n\}/);
    expect(motion).not.toBeNull();
    const outside = css.replace(motion![0], '');
    expect(outside).not.toMatch(/\banimation\s*:/);
    expect(motion![1]).toMatch(/animation: spread-history-draw var\(--dur-draw\) var\(--ease-draw\)/);
    expect(motion![1]).toMatch(/stroke-dashoffset: 1;/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\b\d+px\b/);
  });
});
