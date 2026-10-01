/**
 * @vitest-environment happy-dom
 *
 * Portfolio Builder's failure surfaces in the shared buyer-safe vocabulary
 * (2026-09-21 audit states-04 / states-03), on the mounted route with the real
 * transport errors: the preview and the saved-campaign list render AsyncStatus
 * (describeApiError title and body, one Retry, a 429's countdown), never
 * `error.message`; a failed save says what went wrong for a 403, a 409 and an
 * outage in three different sentences and keeps the typed name.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeAll, beforeEach, describe, expect, it, vi } from 'vitest';
import type { PortfolioPreview } from '../types';
import { ApiError } from '../lib/apiTransport';
import { removeActorScoped } from '../lib/actorScope';
import { preloadAsyncFailure } from '../components/ui/AsyncState';
import { preloadDescribedError } from '../components/ui/DescribedError';
import { CAMPAIGN_DRAFT_KEY } from './portfolio-builder.draft';

const portfolioPreview = vi.fn();
const portfolioCreate = vi.fn();
const campaignRecommendation = vi.fn();
const campaigns = vi.fn();
const salesCampaignPerformance = vi.fn();

// The real transport module (ApiError, isWarmingUpError); only the calls are mocked.
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: {
    portfolioPreview: (...args: unknown[]) => portfolioPreview(...args),
    portfolioCreate: (...args: unknown[]) => portfolioCreate(...args),
    campaignRecommendation: (...args: unknown[]) => campaignRecommendation(...args),
    campaigns: (...args: unknown[]) => campaigns(...args),
    salesCampaignPerformance: (...args: unknown[]) => salesCampaignPerformance(...args),
  },
}));

vi.mock('../lib/configOptionsQuery', () => {
  const value = {
    data: { lender_name: 'Summit Mortgage', target_lender_refs: ['All'], target_lender_refs_status: 'live' },
    isError: false,
  };
  return { useConfigOptionsQuery: () => value };
});

vi.mock('../components/FootprintProvider', () => {
  const value = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => value };
});

vi.mock('../components/AppContext', () => {
  const value = { setDrawer: () => undefined, showEvidence: true, showConfidence: true, canAccessAdmin: false };
  return { useApp: () => value };
});

import PortfolioBuilder from './portfolio-builder';

/** Transport text and server detail that must never reach a buyer. */
const SENTINEL = 'SENTINEL detail 500 Internal Server Error';
const JARGON = /SENTINEL|Internal Server Error|\b500\b|\b503\b|\b403\b|\b409\b|Failed to fetch/;

const PREVIEW = {
  marketable_population: 7_973,
  campaign_build_contact_count: 7_973,
  campaign_build_limit: 10_000,
  campaign_build_eligible: true,
  avg_score: 71,
  top_tier_opportunities: 3_990,
  offers_recommended: 44_700,
  high_intent_leads: 0,
} as unknown as PortfolioPreview;

const apiError = (status: number, extra: Partial<ConstructorParameters<typeof ApiError>[1]> = {}) =>
  new ApiError(SENTINEL, { path: '/api/v1/portfolio/preview', status, ...extra });

let container: HTMLDivElement;
let root: Root;

beforeAll(async () => {
  // The failure half is its own chunk; load it so each render below resolves at once.
  await Promise.all([preloadAsyncFailure(), preloadDescribedError()]);
});

beforeEach(() => {
  for (const mock of [portfolioPreview, portfolioCreate, campaignRecommendation, campaigns, salesCampaignPerformance]) mock.mockReset();
  campaigns.mockResolvedValue({ campaigns: [] });
  portfolioPreview.mockResolvedValue(PREVIEW);
  campaignRecommendation.mockResolvedValue(new Promise(() => undefined));
  salesCampaignPerformance.mockResolvedValue({});
  removeActorScoped('session', CAMPAIGN_DRAFT_KEY);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
});

function mount() {
  const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={['/portfolio-builder']}>
          <PortfolioBuilder />
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

async function waitUntil(condition: () => boolean, ms = 6_000) {
  const start = Date.now();
  while (!condition()) {
    if (Date.now() - start > ms) throw new Error('waitUntil timeout');
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 10));
    });
  }
}

const text = () => container.textContent ?? '';
const alerts = () => [...container.querySelectorAll<HTMLElement>('[role="alert"]')];
const retryButton = (subject: string) => container.querySelector<HTMLButtonElement>(`button[aria-label="Retry loading ${subject}"]`);

describe('the portfolio preview failure (states-04)', () => {
  it('a 500 shows the describeApiError title and Retry, never the status or the server text', async () => {
    portfolioPreview.mockRejectedValueOnce(apiError(500)).mockResolvedValue(PREVIEW);
    mount();
    await waitUntil(() => alerts().some((node) => node.textContent?.includes("Couldn't load portfolio preview")));
    const alert = alerts().find((node) => node.textContent?.includes("Couldn't load portfolio preview"))!;
    expect(alert.textContent).toContain("Couldn't load portfolio preview. The server hit an unexpected error.");
    expect(alert.closest('.mt-4')).not.toBeNull();
    expect(text()).not.toMatch(JARGON);

    act(() => retryButton('portfolio preview')!.click());
    await waitUntil(() => portfolioPreview.mock.calls.length === 2);
    await waitUntil(() => !text().includes("Couldn't load portfolio preview"));
  });

  it('a 429 counts its wait down with Retry inert, then Retry is live', async () => {
    portfolioPreview.mockRejectedValue(apiError(429, { retryable: true, reason: 'rate_limited', retryAfterMs: 2_000 }));
    mount();
    await waitUntil(() => text().includes('Too many requests right now.'));
    expect(text()).toMatch(/Try again in [12] s/);
    const retry = retryButton('portfolio preview')!;
    expect(retry.getAttribute('aria-disabled')).toBe('true');
    act(() => retry.click());
    expect(portfolioPreview).toHaveBeenCalledTimes(1);

    await waitUntil(() => retryButton('portfolio preview')?.getAttribute('aria-disabled') === null, 5_000);
    expect(text()).not.toContain('Try again in');
    expect(text()).not.toMatch(JARGON);
  });
});

describe('the saved-campaign list failure (states-04)', () => {
  it('a 503 is AsyncStatus in the shared vocabulary, and its Retry refetches', async () => {
    campaigns
      .mockRejectedValueOnce(new ApiError(SENTINEL, {
        path: '/api/v1/campaigns',
        status: 503,
        retryable: true,
        dependency: 'lakebase',
        reason: 'retries_exhausted',
      }))
      .mockResolvedValue({ campaigns: [] });
    mount();
    await waitUntil(() => retryButton('saved campaigns') !== null);
    const alert = retryButton('saved campaigns')!.closest<HTMLElement>('[role="alert"]')!;
    expect(alert.textContent).toContain('The app already retried; try again shortly.');
    expect(text()).not.toContain('Saved campaigns unavailable');
    expect(text()).not.toMatch(JARGON);

    act(() => retryButton('saved campaigns')!.click());
    await waitUntil(() => campaigns.mock.calls.length === 2);
    await waitUntil(() => text().includes('No saved campaigns.'));
  });
});

describe('a failed save (states-04)', () => {
  async function failSaveWith(error: unknown): Promise<string> {
    portfolioCreate.mockRejectedValueOnce(error);
    mount();
    const save = () => container.querySelector<HTMLButtonElement>('[data-testid="portfolio-save-build"]')!;
    await waitUntil(() => !save().disabled);
    act(() => save().click());
    const name = container.querySelector<HTMLInputElement>('[data-testid="portfolio-save-name"]')!;
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(name, 'Summit IL refi cohort');
      name.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="portfolio-save-confirm"]')!.click());
    const alert = () => container.querySelector<HTMLElement>('.save-build-form__error[role="alert"]');
    await waitUntil(() => Boolean(alert()?.textContent?.startsWith('Save failed: ')) && !alert()!.textContent!.includes('It could not load.'));
    expect(container.querySelector<HTMLInputElement>('[data-testid="portfolio-save-name"]')?.value).toBe('Summit IL refi cohort');
    const sentence = alert()!.textContent!;
    act(() => root.unmount());
    root = createRoot(container);
    return sentence;
  }

  it('a 403, a 409 and an outage read as three different sentences, each keeping the name', async () => {
    const forbidden = await failSaveWith(new ApiError(SENTINEL, { path: '/api/v1/portfolio/create', status: 403 }));
    const conflict = await failSaveWith(new ApiError(SENTINEL, { path: '/api/v1/portfolio/create', status: 409 }));
    const outage = await failSaveWith(new ApiError(SENTINEL, {
      path: '/api/v1/portfolio/create',
      status: 503,
      retryable: true,
      dependency: 'lakebase',
      reason: 'retries_exhausted',
    }));
    expect(forbidden).toBe('Save failed: Ask an administrator for access. Your name is kept; try again.');
    expect(conflict).toBe('Save failed: Reload to read the current version. Your name is kept; try again.');
    expect(outage).toBe('Save failed: The app already retried; try again shortly. Your name is kept; try again.');
    for (const sentence of [forbidden, conflict, outage]) expect(sentence).not.toMatch(JARGON);
  });
});
