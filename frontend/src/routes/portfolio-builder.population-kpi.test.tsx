/** @vitest-environment happy-dom
 *
 * The population KPI names the predicate it applied. Portfolio Builder builds
 * with CONTACTABILITY = "Eligible only" by default, so the count has the
 * governed contactability gate pushed down: it is the MARKETABLE
 * (contact-eligible) subset, and both the headline and the evidence chip must
 * say so. The chip previously read "Addressable population" — Home's
 * predicate, which is the opposite gate — on a number that had the gate
 * applied.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PortfolioPreview } from '../types';

const portfolioPreview = vi.fn();
const campaigns = vi.fn();
const salesCampaignPerformance = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    portfolioPreview: (...args: unknown[]) => portfolioPreview(...args),
    portfolioCreate: vi.fn(),
    campaignRecommendation: vi.fn(),
    campaigns: (...args: unknown[]) => campaigns(...args),
    salesCampaignPerformance: (...args: unknown[]) => salesCampaignPerformance(...args),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

vi.mock('../lib/configOptionsQuery', () => {
  const value = {
    data: {
      lender_name: 'Summit Mortgage',
      target_lender_refs: ['All'],
      target_lender_refs_status: 'live',
    },
    isError: false,
  };
  return { useConfigOptionsQuery: () => value };
});

vi.mock('../components/FootprintProvider', () => {
  const value = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => value };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canAccessAdmin: false,
  }),
}));

import PortfolioBuilder from './portfolio-builder';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const PREVIEW = {
  marketable_population: 76_487,
  campaign_build_contact_count: 76_487,
  campaign_build_limit: 100_000,
  campaign_build_eligible: true,
  avg_score: 71,
  top_tier_opportunities: 3_990,
  offers_recommended: 44_700,
  high_intent_leads: 1_200,
} as unknown as PortfolioPreview;

describe('PortfolioBuilder population KPI', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    portfolioPreview.mockReset();
    campaigns.mockReset();
    salesCampaignPerformance.mockReset();
    portfolioPreview.mockResolvedValue(PREVIEW);
    campaigns.mockResolvedValue({ campaigns: [] });
    salesCampaignPerformance.mockResolvedValue({
      unique_leads_attempted: 0,
      unique_contacts_reached: 0,
      unique_application_starts: 0,
      unique_applications_submitted: 0,
      unique_closed_funded: 0,
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.clearAllMocks();
  });

  async function mount() {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/portfolio-builder']}>
            <PortfolioBuilder />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    for (let i = 0; i < 20; i += 1) {
      if (container.textContent?.includes('76,487')) break;
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  it('labels the gated count marketable and cites the contact-eligible predicate', async () => {
    await mount();

    const populationCard = [...container.querySelectorAll<HTMLElement>('.kpi')].find((card) =>
      card.querySelector('.kpi__label')?.textContent?.includes('population'),
    );
    expect(populationCard).toBeTruthy();
    expect(populationCard?.querySelector('.kpi__label')?.textContent).toBe('Marketable population');
    expect(populationCard?.querySelector('.kpi__value')?.textContent).toContain('76,487');

    const chip = populationCard?.querySelector('.kpi__source .evidence-chip');
    expect(chip?.textContent).toContain('Marketable population — contact-eligible subset');
    // The addressable chip belongs to Home's ungated count, not this one.
    expect(chip?.textContent).not.toContain('Addressable population');
  });
});
