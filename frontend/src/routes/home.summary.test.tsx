/**
 * @vitest-environment happy-dom
 *
 * S4 acceptance (route grain): Home fetches /api/home/summary alongside the
 * portfolio preview and renders the personalized "since your last login"
 * sentence whose numbers open the EvidenceDrawer citing the snapshot
 * baseline + headline metric view.
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

// One mocked hook, two callers: route the result by react-query key so the
// preview and the summary each see their own payload.
vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: (
    _fetcher: unknown,
    _deps: unknown[],
    opts: { queryKey?: readonly unknown[] } = {},
  ) => {
    const key = (opts.queryKey ?? []).join('.');
    return {
      data: key === 'mip.home.summary' ? SUMMARY : PREVIEW,
      warmingUp: null,
      error: null,
      manualRetry: vi.fn(),
    };
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
vi.mock('../components/mortgage/PortfolioSummaryCard', () => ({
  PortfolioSummaryCard: () => <div data-testid="portfolio-summary-card" />,
}));

vi.mock('../lib/api', () => ({
  api: { portfolioPreview: vi.fn(), homeSummary: vi.fn() },
}));

import Home from './home';

describe('Home renders the personalized last-login summary', () => {
  let root: Root;
  let container: HTMLElement;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    setDrawer.mockClear();
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

  it('shows the delta sentence above the KPI row', () => {
    const summary = container.querySelector('.login-summary');
    expect(summary).toBeTruthy();
    expect(summary?.textContent).toContain('Since your last login');
    const kpiRow = container.querySelector('.kpi-row');
    expect(kpiRow).toBeTruthy();
    expect(
      summary!.compareDocumentPosition(kpiRow!) & Node.DOCUMENT_POSITION_FOLLOWING,
    ).toBeTruthy();
  });

  it('renders each API number verbatim as an evidence affordance', () => {
    const buttons = Array.from(
      container.querySelectorAll<HTMLButtonElement>('.login-summary__num'),
    );
    expect(buttons.map((b) => b.textContent)).toEqual(
      SUMMARY.highlights.map((h) => h.display),
    );
  });

  it('opens the drawer with snapshot + metric-view lineage from the summary', () => {
    const button = container.querySelector<HTMLButtonElement>('.login-summary__num');
    expect(button).toBeTruthy();
    act(() => button!.click());
    expect(setDrawer).toHaveBeenCalledTimes(1);
    const source = setDrawer.mock.calls[0][0] as DrawerSource;
    const signalSources = (source.signals ?? []).map((signal) => signal.source);
    expect(signalSources.some((s) => s.startsWith('kpi_snapshots.'))).toBe(true);
    expect(source.assetPath).toBe('mip.semantics.portfolio_headline_metric_view');
  });
});
