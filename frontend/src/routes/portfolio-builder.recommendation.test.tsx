/** @vitest-environment happy-dom */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  CampaignRecommendationResponse,
  PortfolioPreview,
} from '../types';

const portfolioPreview = vi.fn();
const portfolioCreate = vi.fn();
const campaignRecommendation = vi.fn();
const campaigns = vi.fn();
const salesCampaignPerformance = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    portfolioPreview: (...args: unknown[]) => portfolioPreview(...args),
    portfolioCreate: (...args: unknown[]) => portfolioCreate(...args),
    campaignRecommendation: (...args: unknown[]) => campaignRecommendation(...args),
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
  }),
}));

import PortfolioBuilder from './portfolio-builder';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const PREVIEW = {
  marketable_population: 79_730,
  avg_score: 71,
  top_tier_opportunities: 3_990,
  offers_recommended: 44_700,
  high_intent_leads: 1_200,
} as unknown as PortfolioPreview;

const BENEFIT_PROVENANCE_TOKEN =
  'signed-benefit-provenance-token-000000000000000000000001';
const GUIDANCE_PROVENANCE_TOKEN =
  'signed-guidance-provenance-token-00000000000000000000001';

const RECOMMENDATION: CampaignRecommendationResponse = {
  generation_mode: 'supervisor',
  generator_label: 'Supervisor-generated recommendation',
  performance_status: 'insufficient_sample',
  audience_summary: 'Prime refinance candidates with sufficient equity.',
  strategy: 'Use reviewed benefit and guidance variants.',
  variants: [
    {
      variant_name: 'Benefit-led',
      subject: 'Recommended benefit subject',
      body: 'Recommended benefit body',
      hypothesis: 'Benefit framing improves review starts.',
      provenance_token: BENEFIT_PROVENANCE_TOKEN,
    },
    {
      variant_name: 'Guidance-led',
      subject: 'Recommended guidance subject',
      body: 'Recommended guidance body',
      hypothesis: 'Guidance framing improves qualified replies.',
      provenance_token: GUIDANCE_PROVENANCE_TOKEN,
    },
  ],
  holdout_pct: 10,
  evidence: [],
  warnings: [],
};

describe('PortfolioBuilder recommendation ownership', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    portfolioPreview.mockReset();
    portfolioCreate.mockReset();
    campaignRecommendation.mockReset();
    campaigns.mockReset();
    salesCampaignPerformance.mockReset();
    campaigns.mockResolvedValue({ campaigns: [] });
    portfolioPreview.mockResolvedValue(PREVIEW);
    portfolioCreate.mockResolvedValue({ campaign_id: 'campaign-generated' });
    campaignRecommendation.mockResolvedValue(RECOMMENDATION);
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
  });

  function mount() {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
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

  async function waitUntil(condition: () => boolean, ms = 4_000) {
    const start = Date.now();
    while (!condition()) {
      if (Date.now() - start > ms) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  function field(label: string): HTMLInputElement | HTMLTextAreaElement {
    const match = [...container.querySelectorAll<HTMLLabelElement>('.campaign-setup__field')]
      .find((node) => node.firstElementChild?.textContent === label);
    const input = match?.querySelector<HTMLInputElement | HTMLTextAreaElement>('input, textarea');
    if (!input) throw new Error(`missing campaign field: ${label}`);
    return input;
  }

  function setField(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
    const prototype = input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')?.set;
    if (!setter) throw new Error('missing native value setter');
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  function applyButton(): HTMLButtonElement | undefined {
    return [...container.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('Apply variants'));
  }

  function saveButton(): HTMLButtonElement {
    const button = container.querySelector<HTMLButtonElement>(
      '[data-testid="portfolio-save-build"]',
    );
    if (!button) throw new Error('missing save button');
    return button;
  }

  async function applyAndSaveRecommendation() {
    await waitUntil(() => applyButton()?.disabled === false);
    act(() => applyButton()!.click());
    await waitUntil(() => field('Benefit-led subject').value === RECOMMENDATION.variants[0].subject);
    await waitUntil(() => !saveButton().disabled);
    act(() => saveButton().click());
    const confirm = container.querySelector<HTMLButtonElement>(
      '[data-testid="portfolio-save-confirm"]',
    );
    if (!confirm) throw new Error('missing save confirmation');
    await act(async () => {
      confirm.click();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    await waitUntil(() => portfolioCreate.mock.calls.length === 1);
  }

  it('does not let a deferred recommendation overwrite operator edits before explicit Apply', async () => {
    const recommendation = deferred<CampaignRecommendationResponse>();
    campaignRecommendation.mockReturnValue(recommendation.promise);
    mount();
    await waitUntil(() => campaignRecommendation.mock.calls.length === 1);

    const subject = field('Benefit-led subject');
    setField(subject, 'Operator-owned subject');
    expect(subject.value).toBe('Operator-owned subject');

    recommendation.resolve(RECOMMENDATION);
    await waitUntil(() => applyButton()?.disabled === false);

    expect(field('Benefit-led subject').value).toBe('Operator-owned subject');
    expect(field('Benefit-led message').value).toBe('');

    act(() => applyButton()!.click());
    expect(container.textContent).toContain(
      'Applying this recommendation replaces the campaign copy currently in the editor.',
    );
    const replaceButton = [...container.querySelectorAll<HTMLButtonElement>('button')]
      .find((button) => button.textContent?.includes('Replace edited copy'));
    expect(replaceButton).toBeTruthy();
    act(() => replaceButton!.click());
    expect(field('Benefit-led subject').value).toBe('Recommended benefit subject');
    expect(field('Benefit-led message').value).toBe('Recommended benefit body');
  });

  it('round-trips both signed provenance tokens on an untouched generated save', async () => {
    mount();
    await applyAndSaveRecommendation();

    const config = portfolioCreate.mock.calls[0][2];
    expect(config.message_variants).toEqual([
      expect.objectContaining({
        subject: 'Recommended benefit subject',
        generation_mode: 'supervisor',
        provenance_token: BENEFIT_PROVENANCE_TOKEN,
      }),
      expect.objectContaining({
        subject: 'Recommended guidance subject',
        generation_mode: 'supervisor',
        provenance_token: GUIDANCE_PROVENANCE_TOKEN,
      }),
    ]);
  });

  it('invalidates generated provenance on operator copy edits before save', async () => {
    mount();
    await waitUntil(() => applyButton()?.disabled === false);
    act(() => applyButton()!.click());
    await waitUntil(() => field('Benefit-led subject').value === RECOMMENDATION.variants[0].subject);

    setField(field('Benefit-led subject'), 'Operator revised benefit subject');
    await waitUntil(() => !saveButton().disabled);
    act(() => saveButton().click());
    const confirm = container.querySelector<HTMLButtonElement>(
      '[data-testid="portfolio-save-confirm"]',
    )!;
    await act(async () => {
      confirm.click();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    await waitUntil(() => portfolioCreate.mock.calls.length === 1);

    expect(portfolioCreate.mock.calls[0][2].message_variants).toEqual([
      expect.objectContaining({
        subject: 'Operator revised benefit subject',
        generation_mode: 'operator',
        generator_label: 'Operator edited',
        provenance_token: null,
      }),
      expect.objectContaining({
        generation_mode: 'operator',
        generator_label: 'Operator edited',
        provenance_token: null,
      }),
    ]);
  });
});
