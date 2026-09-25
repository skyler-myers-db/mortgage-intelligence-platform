/**
 * @vitest-environment happy-dom
 *
 * Route-level proof for the 2026-09-21 audit finding flow-v1.
 *
 * Home's "Approval queue" banner quoted the whole-book refinance-economics
 * screen while its button opened the contactable-only Lead Queue: live, 74,335
 * on the banner and 3,217 in the queue footer it linked to. This mounts the
 * HOME ROUTE with the real `useWarmingUpRetry` + react-query wiring and a
 * mocked API, so it proves the whole chain the defect lived in: which
 * endpoint is asked, with which criteria, and what the banner then renders.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  portfolioPreview: vi.fn(),
  homeSummary: vi.fn(),
  // The answer band's WHO column reads the audit-free economics ranking.
  analyticsEconomics: vi.fn(),
  // Neither the banner nor the answer band may reach for the lead list:
  // GET /api/leads writes a VIEW_LEADS audit row per call.
  leads: vi.fn(),
}));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status = 500;
  }
  return {
    api: apiMocks,
    ApiError,
    isAbortError: () => false,
    isWarmingUpError: () => false,
    dependencyLabel: () => 'Warehouse',
  };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({ lender: 'Summit Mortgage', canAccessAdmin: false, setDrawer: vi.fn(), showEvidence: true }),
}));
vi.mock('../components/mortgage/USChoroplethMap', () => ({ USChoroplethMap: () => null }));
vi.mock('../components/mortgage/PinnedInsights', () => ({ PinnedInsights: () => null }));
vi.mock('../components/mortgage/LastLoginSummary', () => ({ LastLoginSummary: () => null }));

import Home from './home';
import {
  APPROVAL_QUEUE_HREF,
  HOME_CONTACTABLE_PREVIEW_CRITERIA,
  requestHomeContactablePreview,
} from './home.approval-banner';

// Live paychex gold, 2026-08-10: the ~23x addressable-vs-contactable gap.
const WHOLE_BOOK_SCREEN = 74_335;
const CONTACTABLE_SCREEN = 3_217;

function previewPayload(highIntent: number) {
  return {
    marketable_population: 5_156_184,
    high_intent_leads: highIntent,
    top_tier_opportunities: 4_351,
    offers_recommended: 4_624_322,
    offers_available: 5_156_184,
    avg_score: 41,
    trends: {},
    trend_status: 'live',
    trend_note: null,
    data_refreshed_at: null,
    approved_count: 159,
    in_outreach_count: 3,
    day_zero: false,
  };
}

type Criteria = { marketing_eligibility?: string };

describe('Home approval-queue banner reconciles with the queue it opens', () => {
  let root: Root;
  let container: HTMLDivElement;
  let queryClient: QueryClient;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false, gcTime: Infinity } } });
    apiMocks.homeSummary.mockResolvedValue(null);
    apiMocks.analyticsEconomics.mockResolvedValue({ top_borrowers: [] });
    apiMocks.portfolioPreview.mockImplementation((criteria: Criteria) =>
      Promise.resolve(
        previewPayload(criteria.marketing_eligibility === 'Eligible only' ? CONTACTABLE_SCREEN : WHOLE_BOOK_SCREEN),
      ),
    );
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    vi.clearAllMocks();
  });

  async function mountHome(): Promise<void> {
    await act(async () => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/']}>
            <Home />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await act(async () => {
      await Promise.resolve();
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  const banner = () => container.querySelector('[role="region"][aria-label="Refinance review queue"]') as HTMLElement;
  const bannerText = () => banner().querySelector('.approval__sub')?.textContent?.replace(/\s+/g, ' ').trim() ?? '';

  // flow-v1 (wave 3): the banner counts the contactable refinance screen,
  // not pending approvals, so it is titled for what it counts and opens.
  it('is titled the refinance review queue, as a region of the same name', async () => {
    await mountHome();
    expect(banner().querySelector('.approval__title')?.textContent).toBe('Refinance review queue');
    expect(banner().querySelector('a')?.getAttribute('href')).toBe('/lead-queue?segment=itm');
    expect(container.textContent).not.toContain('Approval queue');
  });

  it('states the contactable count beside the whole-book screen, SegmentCard style', async () => {
    await mountHome();
    expect(bannerText()).toBe(
      '3,217 contactable of 74,335 borrowers passing the refinance-economics screen. '
      + '159 approved and 3 in outreach in current lifecycle state.',
    );
    expect(banner().querySelector('[data-testid="approval-queue-contactable"]')?.textContent).toBe('3,217');
    expect(banner().querySelector('[data-testid="approval-queue-screen"]')?.textContent).toBe('74,335');
  });

  it('sources N from the queue\'s own default filter, and M from the KPI card above it', async () => {
    await mountHome();
    const criteria = apiMocks.portfolioPreview.mock.calls.map(([c]) => c as Criteria);
    // N: the Lead Queue defaults to "Eligible only"; so does the count.
    expect(criteria).toContainEqual({ marketing_eligibility: 'Eligible only' });
    expect(HOME_CONTACTABLE_PREVIEW_CRITERIA).toEqual({ marketing_eligibility: 'Eligible only' });
    // M is the same whole-book preview value the KPI row renders.
    const kpi = [...container.querySelectorAll('.kpi')].find(
      (card) => card.querySelector('.kpi__label')?.textContent === 'Refi economics screen',
    );
    expect(kpi?.querySelector('.kpi__value')?.textContent).toBe('74,335');
    expect(banner().querySelector('[data-testid="approval-queue-screen"]')?.textContent).toBe('74,335');
    // The link still opens the governed, contactable-only queue.
    expect(banner().querySelector('a')?.getAttribute('href')).toBe(APPROVAL_QUEUE_HREF);
    expect(APPROVAL_QUEUE_HREF).toBe('/lead-queue?segment=itm');
  });

  it('never reads the lead list to get its count (that read writes a VIEW_LEADS audit row)', async () => {
    await mountHome();
    expect(apiMocks.leads).not.toHaveBeenCalled();
    // The answer band's WHO column is mounted for real here and takes its
    // ranking from the audit-free economics read instead.
    expect(apiMocks.analyticsEconomics).toHaveBeenCalled();
    const signal = new AbortController().signal;
    await requestHomeContactablePreview(signal);
    expect(apiMocks.portfolioPreview).toHaveBeenLastCalledWith({ marketing_eligibility: 'Eligible only' }, signal);
    expect(apiMocks.leads).not.toHaveBeenCalled();
  });

  it('does not imply a queue size while the contactable count is unknown', async () => {
    apiMocks.portfolioPreview.mockImplementation((criteria: Criteria) =>
      criteria.marketing_eligibility === 'Eligible only'
        ? Promise.reject(new Error('contactable preview failed'))
        : Promise.resolve(previewPayload(WHOLE_BOOK_SCREEN)),
    );
    await mountHome();
    expect(bannerText()).toBe(
      '74,335 borrowers pass the refinance-economics screen across the whole book; the review queue lists '
      + 'only the contactable subset. 159 approved and 3 in outreach in current lifecycle state.',
    );
    expect(banner().querySelector('[data-testid="approval-queue-contactable"]')).toBeNull();
    // The failed count never stacks a second error callout on Home.
    expect(container.querySelector('[role="alert"]')).toBeNull();
  });

  it('says nothing to reconcile when every borrower passing the screen is contactable', async () => {
    apiMocks.portfolioPreview.mockResolvedValue(previewPayload(CONTACTABLE_SCREEN));
    await mountHome();
    expect(bannerText()).toBe(
      '3,217 borrowers pass the refinance-economics screen. 159 approved and 3 in outreach in current lifecycle state.',
    );
    expect(bannerText()).not.toContain('contactable of');
  });

  it('clamps a contactable count that straddled a refresh so N can never exceed M', async () => {
    apiMocks.portfolioPreview.mockImplementation((criteria: Criteria) =>
      Promise.resolve(previewPayload(criteria.marketing_eligibility === 'Eligible only' ? 80_000 : WHOLE_BOOK_SCREEN)),
    );
    await mountHome();
    expect(bannerText()).not.toContain('80,000');
    expect(bannerText()).toContain('74,335 borrowers pass the refinance-economics screen.');
  });

  it('keeps the neutral copy while the whole-book preview has not loaded', async () => {
    apiMocks.portfolioPreview.mockImplementation(() => new Promise(() => {}));
    await mountHome();
    expect(bannerText()).toBe(
      'Borrowers passing the refinance-economics screen are ready for loan-officer review.',
    );
  });
});
