/**
 * @vitest-environment happy-dom
 *
 * Rendered-layer pins for the "Why now" rate window (dataviz-08 /
 * dataviz-06): two stacked panels on one axis, the spread sentence, the
 * labelled refi-screen line, the evidence chip's drawer destination, the
 * table alternative and the warming-up degraded state.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { RateWindowResponse } from '../types';
import type { WarmingUpState } from '../lib/useWarmingUpRetry';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

let hookData: RateWindowResponse | null = null;
let hookWarming: WarmingUpState | null = null;

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: hookData,
    warmingUp: hookWarming,
    error: null,
    isFetching: false,
    isPlaceholderData: false,
    manualRetry: vi.fn(),
  }),
}));

const setDrawer = vi.fn();
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage', setDrawer, showEvidence: true }),
}));

vi.mock('../lib/api', () => ({
  api: { analyticsRateWindow: vi.fn() },
}));

import { DRAWER_SOURCES } from '../lib/drawerSources';
import { RateWindowPanel, RateWindowSection } from './analytics.rate-window';

// Twelve Mondays from 2026-01-05; the last print (6.22%) against the 7.10%
// median is the 88 bps the sentence must state, and 900 + 11 * 96 = 1,956.
const WEEKS = Array.from({ length: 12 }, (_, idx) => ({
  week: new Date(Date.UTC(2026, 0, 5 + idx * 7)).toISOString().slice(0, 10),
  market_rate_pct: idx === 11 ? 6.22 : Number((7.1 - 0.08 * idx).toFixed(2)),
  book_median_pct: 7.1,
  book_p25_pct: 6.55,
  book_p75_pct: 7.62,
  itm_count: 900 + idx * 96,
  is_latest: idx === 11,
}));

const RESPONSE: RateWindowResponse = {
  series_id: 'MORTGAGE30US',
  weeks: WEEKS,
  book_lien_count: 48_210,
  book_as_of: '2026-04-16 06:00:00',
  thresholds: { min_spread_bps: 75, min_equity_pct: 15 },
  provenance: {
    market_rate_source: 'mip.silver.market_rates_weekly',
    book_source: 'mip.gold.borrower_360 + mip.silver.lien_current',
    gold_source: 'mip.gold.rate_window_weekly',
    rule_source: 'mip.gold.fn_rate_spread + mip.gold.fn_in_the_money',
    book_as_of: '2026-04-16 06:00:00',
    refreshed_at: '2026-04-16 06:00:00',
    note: 'test',
  },
};

describe('RateWindowPanel', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    setDrawer.mockClear();
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(data: RateWindowResponse = RESPONSE): void {
    act(() => {
      root.render(<RateWindowPanel data={data} />);
    });
  }

  it('draws two stacked panels on one shared axis and says what the chart means', () => {
    render();
    expect(container.querySelectorAll('.rate-window__panel svg')).toHaveLength(2);
    const summary = container.querySelector('[data-testid="rate-window-summary"]');
    expect(summary?.textContent).toContain("The 30-year is 88 bps below the book's median note rate.");
    expect(summary?.textContent).toContain("1,956 of 48,210 fixed-rate liens clear the refi screen at this week's rate.");
    // dataviz-06: a labelled reference line, not just a stroke.
    expect(container.querySelector('[data-testid="rate-window-threshold"]')?.textContent)
      .toBe('Refi screen: 75 bps below the book median (6.35%)');
    expect(container.querySelectorAll('line.rate-window__threshold')).toHaveLength(1);
    expect(container.querySelector('[data-testid="rate-window-current"]')?.textContent).toContain('6.22%');
    // Only the bottom panel carries the week ticks: one x-axis, never two.
    expect(container.querySelectorAll('.rate-window__panel--rates .analytics-chart__x-ticks')).toHaveLength(0);
    expect(container.querySelectorAll('.rate-window__panel--itm .analytics-chart__tick--x').length).toBeGreaterThan(1);
    // The disclosure names today's book against the historical rate.
    expect(container.querySelector('[data-testid="rate-window-asof"]')?.textContent).toContain("Today's book (as of");
    // The image has an accessible description carrying the same sentence.
    expect(container.querySelector('.rate-window__panels')?.getAttribute('aria-label')).toContain('88 bps below');
  });

  it('offers a table alternative listing the same weeks', () => {
    render();
    const toggle = container.querySelector<HTMLButtonElement>('button[aria-pressed]');
    expect(toggle?.textContent).toBe('View as table');
    act(() => toggle?.click());
    expect(container.querySelectorAll('.rate-window__panel svg')).toHaveLength(0);
    const rows = container.querySelectorAll('[data-testid="rate-window-table"] tbody tr');
    expect(rows).toHaveLength(WEEKS.length);
    expect(rows[0].textContent).toContain(WEEKS[0].week);
    expect(rows[rows.length - 1].textContent).toContain('(current)');
    expect(rows[rows.length - 1].textContent).toContain('1,956');
    act(() => container.querySelector<HTMLButtonElement>('button[aria-pressed]')?.click());
    expect(container.querySelectorAll('.rate-window__panel svg')).toHaveLength(2);
  });

  it('opens the evidence drawer on the rate-window destination citing both source tables', () => {
    render();
    act(() => container.querySelector<HTMLButtonElement>('.evidence-chip')?.click());
    expect(setDrawer).toHaveBeenCalledWith(DRAWER_SOURCES.rateWindow);
    expect(DRAWER_SOURCES.rateWindow.assetPath).toBe('mip.gold.rate_window_weekly');
    const cited = (DRAWER_SOURCES.rateWindow.signals ?? []).map((s) => s.source);
    expect(cited).toContain('mip.silver.market_rates_weekly');
    expect(cited).toContain('mip.gold.rate_window_weekly');
  });

  it('draws no threshold line and no band when the book is empty', () => {
    const empty: RateWindowResponse = {
      ...RESPONSE,
      book_lien_count: 0,
      thresholds: { min_spread_bps: null, min_equity_pct: null },
      weeks: WEEKS.map((w) => ({ ...w, book_median_pct: null, book_p25_pct: null, book_p75_pct: null, itm_count: 0 })),
    };
    render(empty);
    expect(container.querySelectorAll('.rate-window__panel svg')).toHaveLength(2);
    expect(container.querySelector('[data-testid="rate-window-threshold"]')).toBeNull();
    expect(container.querySelectorAll('polygon.rate-window__band')).toHaveLength(0);
    expect(container.querySelector('[data-testid="rate-window-summary"]')?.textContent).toContain('empty');
  });
});

describe('RateWindowSection', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    hookData = null;
    hookWarming = null;
  });

  it('renders the WarmingUpBlock and no chart while the warehouse warms up', () => {
    hookData = null;
    hookWarming = { dependency: 'warehouse', label: 'Warehouse warming up', attempt: 1, maxAttempts: 6, correlationId: null };
    act(() => {
      root.render(<RateWindowSection />);
    });
    expect(container.querySelector('[data-testid="warming-up-block"]')).not.toBeNull();
    expect(container.querySelector('[data-testid="rate-window"]')).toBeNull();
    expect(container.querySelectorAll('.rate-window__panel svg')).toHaveLength(0);
  });

  it('renders the panel once data arrives', () => {
    hookData = RESPONSE;
    act(() => {
      root.render(<RateWindowSection />);
    });
    expect(container.querySelector('[data-testid="rate-window"]')).not.toBeNull();
    expect(container.querySelectorAll('.rate-window__panel svg')).toHaveLength(2);
  });
});
