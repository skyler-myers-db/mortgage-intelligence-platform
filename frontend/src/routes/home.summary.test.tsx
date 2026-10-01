/**
 * @vitest-environment happy-dom
 *
 * S4 acceptance (route grain): Home fetches /api/home/summary alongside the
 * portfolio preview and renders the personalized "since your last login"
 * changes as the answer band's WHY NOW column (2026-09-21 audit flow-05),
 * whose tokens open the EvidenceDrawer citing the snapshot baseline +
 * headline metric view.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../components/AppContext';
import type { HomeSummary } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PREVIEW = {
  marketable_population: 12,
  high_intent_leads: 7,
  top_tier_opportunities: 5,
  offers_recommended: 6,
  offers_available: 9,
  avg_score: 80,
  trends: {},
  trend_status: 'live',
  trend_note: null,
  data_refreshed_at: null,
  approved_count: 2,
  in_outreach_count: 1,
  day_zero: false,
};

const SUMMARY: HomeSummary = {
  status: 'delta',
  previous_visit_at: '2026-07-09T14:30:00+00:00',
  baseline_snapshot_at: '2026-07-09T06:00:00+00:00',
  headline:
    'Since your last login: +1.5% high-opportunity, +2,250 refi candidates, +4,120 offers available.',
  phrasing_source: 'deterministic',
  phrasing_fallback_reason: 'genie_not_configured',
  highlights: [
    {
      measure: 'high_opportunity',
      label: 'high-opportunity',
      display: '+1.5%',
      value_token: '+1.5%',
      current: 88210,
      baseline: 86900,
      delta: 1310,
      delta_pct: 1.5,
    },
    {
      measure: 'refi_economics_screen',
      label: 'refi candidates',
      display: '+2,250',
      value_token: '+2,250',
      current: 261400,
      baseline: 259150,
      delta: 2250,
      delta_pct: 0.9,
    },
    {
      measure: 'offers_available',
      label: 'offers available',
      display: '+4,120',
      value_token: '+4,120',
      current: 402330,
      baseline: 398210,
      delta: 4120,
      delta_pct: 1.0,
    },
  ],
  current: {},
  baseline: {},
  deltas: {},
  current_source: 'mip.semantics.portfolio_headline_metric_view',
  baseline_source: 'mip_app.kpi_snapshots',
};

// One mocked hook, several callers: route the result by react-query key so
// the preview and the summary each see their own payload (and their own
// fetch time, error and Refresh), and any other read on Home stays pending.
interface HeroRead {
  data: unknown;
  warmingUp: null;
  error: Error | null;
  manualRetry: () => void;
  isFetching: boolean;
  isPlaceholderData: boolean;
  dataUpdatedAt: number | null;
  errorUpdatedAt: number | null;
}
const heroRead = (data: unknown, overrides: Partial<HeroRead> = {}): HeroRead => ({
  data,
  warmingUp: null,
  error: null,
  manualRetry: vi.fn(),
  isFetching: false,
  isPlaceholderData: false,
  dataUpdatedAt: Date.parse('2026-10-01T09:00:00Z'),
  errorUpdatedAt: null,
  ...overrides,
});
const reads = vi.hoisted(() => ({ byKey: new Map<string, unknown>() }));
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (
    _fetcher: unknown,
    opts: { queryKey: readonly unknown[] },
  ) => reads.byKey.get(opts.queryKey.join('.')) ?? {
    data: null, warmingUp: null, error: null, manualRetry: () => undefined, isFetching: true,
    isPlaceholderData: false, dataUpdatedAt: null, errorUpdatedAt: null,
  },
}));

const setDrawer = vi.fn();
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage', setDrawer, showEvidence: true }),
}));

vi.mock('../components/mortgage/USChoroplethMap', () => ({
  USChoroplethMap: () => <div data-testid="us-choropleth-map" />,
}));
vi.mock('../components/mortgage/PinnedInsights', () => ({
  PinnedInsights: () => <div data-testid="pinned-insights" />,
}));
vi.mock('../components/mortgage/HomeAnswerWho', () => ({
  HomeAnswerWho: () => <div data-testid="home-answer-who" />,
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: { portfolioPreview: vi.fn(), homeSummary: vi.fn() },
}));

import Home from './home';

const PREVIEW_KEY = 'mip.portfolio.preview.home';
const SUMMARY_KEY = 'mip.home.summary';

describe('Home renders the personalized last-login summary', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    setDrawer.mockClear();
    reads.byKey.set(PREVIEW_KEY, heroRead(PREVIEW));
    reads.byKey.set(SUMMARY_KEY, heroRead(SUMMARY));
    act(() => {
      root.render(
        <MemoryRouter>
          <Home />
        </MemoryRouter>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('shows the last-login changes inside the answer band, below the full-width KPI row', () => {
    const summary = container.querySelector('.home-answer .login-summary');
    expect(summary).toBeTruthy();
    expect(summary?.textContent).toContain('Since your last login');
    const kpiRow = container.querySelector('.kpi-row');
    expect(kpiRow).toBeTruthy();
    expect(
      kpiRow!.compareDocumentPosition(summary!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('renders each API number verbatim as an evidence affordance', () => {
    const buttons = Array.from(
      container.querySelectorAll<HTMLButtonElement>('.login-summary .evidence-chip'),
    );
    expect(buttons.map((b) => b.textContent)).toEqual(
      SUMMARY.highlights.map((h) => h.display),
    );
  });

  it('opens the drawer with snapshot + metric-view lineage from the summary', () => {
    const button = container.querySelector<HTMLButtonElement>('.login-summary .evidence-chip');
    expect(button).toBeTruthy();
    act(() => button!.click());
    expect(setDrawer).toHaveBeenCalledTimes(1);
    const source = setDrawer.mock.calls[0][0] as DrawerSource;
    const signalSources = (source.signals ?? []).map((signal) => signal.source);
    expect(signalSources.some((s) => s.startsWith('kpi_snapshots.'))).toBe(true);
    expect(source.assetPath).toBe('mip.semantics.portfolio_headline_metric_view');
  });

  // states-09 / delivery-05 (the W5a ruling): the briefing says how old it
  // is, a restored snapshot included, and one Refresh re-reads both reads.
  function renderAgain() {
    act(() => {
      root.render(
        <MemoryRouter>
          <Home />
        </MemoryRouter>,
      );
    });
  }
  const fetchedAt = () => container.querySelector('[data-testid="fetched-at"]');
  /** The stale note is its own chunk (StaleDataNote.lazy): let it land. */
  async function settleLazy() {
    for (let i = 0; i < 20; i += 1) {
      await act(async () => {
        await vi.dynamicImportSettled();
        await new Promise((resolve) => setTimeout(resolve, 5));
      });
    }
  }

  it('shows the age of the OLDEST hero read in the hero, before the one primary action', () => {
    const restoredAt = Date.parse('2026-10-01T06:00:00Z');
    reads.byKey.set(SUMMARY_KEY, heroRead(SUMMARY, { dataUpdatedAt: restoredAt, isFetching: true }));
    renderAgain();
    const el = fetchedAt();
    expect(el?.querySelector('time')?.getAttribute('dateTime')).toBe(new Date(restoredAt).toISOString());
    // A restored value bridging its refresh: the true age plus "Refreshing…".
    expect(el?.querySelector('button')?.textContent).toBe('Refreshing…');
    const primary = container.querySelectorAll('.btn--primary');
    expect(primary).toHaveLength(1);
    expect(el && primary[0] && el.compareDocumentPosition(primary[0]) & Node.DOCUMENT_POSITION_FOLLOWING).toBeTruthy();
  });

  it('Refresh re-reads both hero reads', () => {
    const preview = heroRead(PREVIEW);
    const summary = heroRead(SUMMARY);
    reads.byKey.set(PREVIEW_KEY, preview);
    reads.byKey.set(SUMMARY_KEY, summary);
    renderAgain();
    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Refresh today\'s briefing"]')?.click());
    expect(preview.manualRetry).toHaveBeenCalledTimes(1);
    expect(summary.manualRetry).toHaveBeenCalledTimes(1);
  });

  it('marks a refresh that failed over the data on screen with that data\'s age, once', async () => {
    expect(container.querySelector('[data-testid="stale-data-note"]')).toBeNull();
    const shownAt = Date.parse('2026-10-01T07:00:00Z');
    reads.byKey.set(SUMMARY_KEY, heroRead(SUMMARY, { dataUpdatedAt: shownAt, error: new Error('refresh failed') }));
    renderAgain();
    await settleLazy();
    const notes = container.querySelectorAll('[data-testid="stale-data-note"]');
    expect(notes).toHaveLength(1);
    expect(notes[0].querySelector('time')?.getAttribute('dateTime')).toBe(new Date(shownAt).toISOString());
    expect(notes[0].closest('.home-stack')).not.toBeNull();
    // The summary's values stay up under the note.
    expect(container.querySelector('.login-summary')?.textContent).toContain('+2,250');
  });

  it('a failure with nothing on screen is not "stale": no note, even with an earlier fetch time on record', async () => {
    reads.byKey.set(SUMMARY_KEY, heroRead(null, { error: new Error('down'), dataUpdatedAt: Date.parse('2026-10-01T07:00:00Z') }));
    renderAgain();
    await settleLazy();
    expect(container.querySelector('[data-testid="stale-data-note"]')).toBeNull();
  });
});
