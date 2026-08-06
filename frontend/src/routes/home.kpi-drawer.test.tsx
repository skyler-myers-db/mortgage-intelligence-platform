/**
 * @vitest-environment happy-dom
 *
 * S1 acceptance: every home headline KPI opens the EvidenceDrawer citing the
 * portfolio headline metric view plus its underlying row asset. The KPI row
 * renders from a loaded preview; each `.kpi__source` EvidenceChip must hand
 * the AppContext drawer a source whose lineage names
 * mip.semantics.portfolio_headline_metric_view.
 */

import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { DrawerSource } from '../components/AppContext';

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

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: PREVIEW,
    warmingUp: null,
    error: null,
    manualRetry: vi.fn(),
  }),
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
  api: { portfolioPreview: vi.fn() },
}));

import Home from './home';

describe('Home KPI evidence drawers cite the headline metric view', () => {
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

  const KPI_CHIPS: Array<{ label: string; chipText: string; family: string }> = [
    { label: 'Marketable population', chipText: 'Marketable population', family: 'marketable_population' },
    { label: 'Refi economics screen', chipText: 'Rate + equity screen', family: 'in_the_money' },
    { label: 'Opportunity score 75+', chipText: 'Opportunity score', family: 'opportunity_score' },
    { label: 'Primary offer paths', chipText: 'How the offer path was selected', family: 'next_best_offer' },
  ];

  it('renders all four headline KPI cards with evidence chips', () => {
    const labels = Array.from(container.querySelectorAll('.kpi__label')).map(
      (node) => node.textContent,
    );
    for (const { label } of KPI_CHIPS) {
      expect(labels).toContain(label);
    }
    expect(container.querySelectorAll('.kpi__source').length).toBe(4);
  });

  for (const { label, chipText, family } of KPI_CHIPS) {
    it(`opens the drawer for "${label}" citing the metric view + underlying rows`, () => {
      const chip = Array.from(
        container.querySelectorAll<HTMLButtonElement>('.kpi__source button'),
      ).find((btn) => btn.textContent?.includes(chipText));
      expect(chip, `evidence chip for ${label}`).toBeTruthy();

      act(() => chip!.click());

      expect(setDrawer).toHaveBeenCalledTimes(1);
      const source = setDrawer.mock.calls[0][0] as DrawerSource;
      // The metric-view citation renders from the governed manifest family
      // (EvidenceDrawer.headline-evidence.test.tsx pins that end to end);
      // the payload's contract is the right family + a governed row anchor.
      expect(source.lineageFamily).toBe(family);
      // Underlying rows: the drawer stays anchored to a governed row-grain
      // asset so freshness + row citations resolve.
      expect(source.assetKey).toBeTruthy();
      setDrawer.mockClear();
    });
  }
});
