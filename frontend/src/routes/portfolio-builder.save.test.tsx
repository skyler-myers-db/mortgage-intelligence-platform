/**
 * @vitest-environment happy-dom
 *
 * "Save build" contract (re-audit #3 P1, 2026-06-12): clicking Save
 * during an in-flight build froze the tab because the handler opened a
 * SYNCHRONOUS window.prompt() — a renderer-blocking native dialog that
 * also hangs any CDP/Playwright session — and the disabled-guard used
 * `preview === null` as its only "build running" signal, which is false
 * during a background refetch of an unchanged key. Pins:
 *
 * 1. while the build fetch is in flight, Save is disabled
 *    ("Build running…") — the audit's Run→Save race is unclickable;
 * 2. Save opens an IN-PAGE naming form — window.prompt is never called;
 * 3. submitting the form creates the campaign with the typed name.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the route source under Vitest only (same pattern as
// design-system/components.test.ts).
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import type { PortfolioPreview } from '../types';

// Vitest executes on Node, but the app tsconfig (correctly) carries no Node
// globals — declare the one we use.
declare const process: { cwd(): string };

const portfolioPreview = vi.fn();
const portfolioCreate = vi.fn();
const campaigns = vi.fn();
const salesCampaignPerformance = vi.fn();
const campaignRecommendation = vi.fn();

vi.mock('../lib/api', () => ({
  api: {
    portfolioPreview: (...args: unknown[]) => portfolioPreview(...args),
    portfolioCreate: (...args: unknown[]) => portfolioCreate(...args),
    campaigns: (...args: unknown[]) => campaigns(...args),
    salesCampaignPerformance: (...args: unknown[]) => salesCampaignPerformance(...args),
    campaignRecommendation: (...args: unknown[]) => campaignRecommendation(...args),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

// Mock providers MUST return identity-stable values: the component keys
// memos and effects on these objects, and a fresh object per render is an
// unbounded re-render loop (this exact mock spun a vitest worker at 100%
// CPU before the identity-preserving guard landed in the component).
vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = {
    data: {
      lender_name: 'Summit Mortgage',
      target_lender_refs: ['All'],
      target_lender_refs_status: 'live',
    },
    isError: false,
  };
  return { useConfigOptionsQuery: () => STABLE };
});

vi.mock('../components/FootprintProvider', () => {
  const STABLE = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => STABLE };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

import PortfolioBuilder from './portfolio-builder';

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

const PREVIEW: PortfolioPreview = {
  marketable_population: 79730,
  avg_score: 71,
  top_tier_opportunities: 3990,
  offers_recommended: 44700,
  high_intent_leads: 1200,
} as unknown as PortfolioPreview;

describe('PortfolioBuilder save-build flow', () => {
  let container: HTMLDivElement;
  let root: Root;
  let promptSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    vi.clearAllMocks();
    portfolioPreview.mockReset();
    portfolioCreate.mockReset();
    campaigns.mockReset();
    salesCampaignPerformance.mockReset();
    campaignRecommendation.mockReset();
    campaigns.mockResolvedValue({ campaigns: [] });
    salesCampaignPerformance.mockResolvedValue({
      from_date: '2026-04-01',
      to_date: '2026-06-30',
      unique_leads_attempted: 100,
      unique_contacts_reached: 40,
      unique_application_starts: 10,
      unique_applications_submitted: 8,
      unique_closed_funded: 3,
      methodology: 'same_borrower_nested_funnel',
    });
    campaignRecommendation.mockResolvedValue({
      generation_mode: 'supervisor',
      generator_label: 'Mortgage Growth Supervisor',
      performance_status: 'qualified',
      audience_summary: 'Selected cohort.',
      strategy: 'Use reviewed copy.',
      variants: [
        {
          variant_name: 'A',
          subject: 'A',
          body: 'A body',
          hypothesis: 'A hypothesis',
          provenance_token: null,
        },
        {
          variant_name: 'B',
          subject: 'B',
          body: 'B body',
          hypothesis: 'B hypothesis',
          provenance_token: null,
        },
      ],
      holdout_pct: 10,
      evidence: [],
      warnings: [],
    });
    promptSpy = vi.fn();
    // If ANY code path reaches for the native blocking dialog again, fail
    // loudly instead of freezing a renderer at the booth.
    vi.stubGlobal('prompt', promptSpy);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
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

  function saveButton(): HTMLButtonElement {
    const btn = container.querySelector<HTMLButtonElement>(
      '[data-testid="portfolio-save-build"]',
    );
    if (!btn) throw new Error('save button not rendered');
    return btn;
  }

  async function flush() {
    await act(async () => {
      await Promise.resolve();
    });
  }

  /** Poll until the react-query chain settles into `cond` (mock promises
   * resolve across several microtask turns; a single flush can land
   * mid-chain). */
  async function waitUntil(cond: () => boolean, ms = 4000) {
    const start = Date.now();
    while (!cond()) {
      if (Date.now() - start > ms) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((r) => setTimeout(r, 10));
      });
    }
  }

  it('disables Save while the build fetch is in flight', async () => {
    const inflight = deferred<PortfolioPreview>();
    portfolioPreview.mockReturnValue(inflight.promise);
    mount();
    await flush();

    expect(saveButton().disabled).toBe(true);
    expect(saveButton().textContent).toContain('Build running…');

    inflight.resolve(PREVIEW);
    await waitUntil(() => !saveButton().disabled);

    expect(saveButton().textContent).toContain('Save build');
  });

  it('opens an in-page naming form instead of window.prompt and saves with the typed name', async () => {
    portfolioPreview.mockResolvedValue(PREVIEW);
    portfolioCreate.mockResolvedValue({ campaign_id: 'c-1' });
    mount();
    await waitUntil(() => !saveButton().disabled);

    act(() => {
      saveButton().click();
    });

    expect(promptSpy).not.toHaveBeenCalled();
    const nameInput = container.querySelector<HTMLInputElement>(
      '[data-testid="portfolio-save-name"]',
    );
    expect(nameInput).not.toBeNull();
    expect(nameInput!.value).toContain('Portfolio build');

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(
        HTMLInputElement.prototype,
        'value',
      )!.set!;
      setter.call(nameInput!, 'Booth build — Summit IL refi');
      nameInput!.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="portfolio-save-confirm"]')!
        .click();
    });
    await waitUntil(() => portfolioCreate.mock.calls.length > 0);
    await flush();

    expect(portfolioCreate).toHaveBeenCalledTimes(1);
    expect(portfolioCreate.mock.calls[0][0]).toBe('Booth build — Summit IL refi');
    expect(portfolioCreate.mock.calls[0][2].message_variants).toEqual([]);
    expect(promptSpy).not.toHaveBeenCalled();
    // The panel closes and the hint flips to saved.
    expect(
      container.querySelector('[data-testid="portfolio-save-name"]'),
    ).toBeNull();
    expect(saveButton().textContent).toContain('Build saved');
  });

  it('explains incomplete campaign copy and does not submit an invalid save', async () => {
    portfolioPreview.mockResolvedValue(PREVIEW);
    mount();
    await waitUntil(() => !saveButton().disabled);

    const subject = container.querySelector<HTMLInputElement>(
      'input[aria-label="Benefit-led subject"]',
    );
    expect(subject).not.toBeNull();
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(subject!, 'Review your mortgage options');
      subject!.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => saveButton().click());
    act(() => {
      container
        .querySelector<HTMLButtonElement>('[data-testid="portfolio-save-confirm"]')!
        .click();
    });

    await flush();
    expect(portfolioCreate).not.toHaveBeenCalled();
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'Benefit-led copy needs both a subject and message before this build can be saved.',
    );
  });

  it('keeps Save unavailable while a changed build replaces stale preview data', async () => {
    const nextBuild = deferred<PortfolioPreview>();
    portfolioPreview.mockResolvedValueOnce(PREVIEW).mockReturnValue(nextBuild.promise);
    mount();
    await waitUntil(() => !saveButton().disabled);

    const occupancy = container.querySelector<HTMLButtonElement>(
      'button[aria-label^="OCCUPANCY:"]',
    )!;
    act(() => occupancy.click());
    const nonOwner = [...container.querySelectorAll<HTMLElement>('[role="option"]')].find(
      (option) => option.textContent?.includes('Non-owner-occupied'),
    )!;
    act(() => nonOwner.click());
    expect(saveButton().disabled).toBe(true);
    expect(saveButton().textContent).toContain('Run before saving');

    const runBuild = [...container.querySelectorAll<HTMLButtonElement>('button')].find(
      (button) => button.textContent?.includes('Run build'),
    )!;
    act(() => runBuild.click());
    await waitUntil(() => portfolioPreview.mock.calls.length >= 2);
    expect(saveButton().disabled).toBe(true);
    expect(saveButton().textContent).toContain('Build running…');
    act(() => saveButton().click());
    expect(container.querySelector('[data-testid="portfolio-save-name"]')).toBeNull();

    nextBuild.resolve(PREVIEW);
    await waitUntil(() => !saveButton().disabled);
    expect(saveButton().textContent).toContain('Save build');
  });

  it('preserves the typed name and idempotency key across a failed save retry', async () => {
    portfolioPreview.mockResolvedValue(PREVIEW);
    portfolioCreate
      .mockRejectedValueOnce(new Error('Lakebase unavailable'))
      .mockResolvedValueOnce({ campaign_id: 'c-retry' });
    mount();
    await waitUntil(() => !saveButton().disabled);

    act(() => saveButton().click());
    const nameInput = container.querySelector<HTMLInputElement>(
      '[data-testid="portfolio-save-name"]',
    )!;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!;
      setter.call(nameInput, 'Distinct Illinois refinance cohort');
      nameInput.dispatchEvent(new Event('input', { bubbles: true }));
    });
    const confirm = container.querySelector<HTMLButtonElement>(
      '[data-testid="portfolio-save-confirm"]',
    )!;
    act(() => confirm.click());
    await waitUntil(() => container.querySelector('[role="alert"]') !== null);

    expect(nameInput.value).toBe('Distinct Illinois refinance cohort');
    expect(container.querySelector('[role="alert"]')?.textContent).toContain(
      'Save failed — your name is kept; try again.',
    );
    const firstRequestId = portfolioCreate.mock.calls[0][2].request_id;

    act(() => confirm.click());
    await waitUntil(() => portfolioCreate.mock.calls.length === 2);
    await waitUntil(() => container.querySelector('[data-testid="portfolio-save-name"]') === null);
    expect(portfolioCreate.mock.calls[1][2].request_id).toBe(firstRequestId);
    expect(saveButton().textContent).toContain('Build saved');
  });

  it('launches a saved Supervisor campaign with its selected variant and provenance', async () => {
    portfolioPreview.mockResolvedValue(PREVIEW);
    const campaignId = '11111111-1111-4111-8111-111111111111';
    campaigns.mockResolvedValue({
      campaigns: [
        {
          campaign_id: campaignId,
          name: 'Supervisor IL refinance campaign',
          owner_email: 'growth@summit.example',
          status: 'draft',
          criteria: { states: ['IL'], marketing_eligibility: 'Eligible only' },
          message_variants: [
            {
              variant_name: 'A',
              channel: 'email',
              subject: 'A',
              body: 'A body',
              generation_mode: 'supervisor',
              generator_label: 'Mortgage Growth Supervisor',
              provenance_token: 'signed-variant-proof-token-0000000000001',
            },
            {
              variant_name: 'B',
              channel: 'email',
              subject: 'B',
              body: 'B body',
              generation_mode: 'supervisor',
              generator_label: 'Mortgage Growth Supervisor',
              provenance_token: 'signed-variant-proof-token-0000000000002',
            },
          ],
        },
      ],
    });
    mount();
    await waitUntil(() => container.textContent?.includes('Supervisor IL refinance campaign') === true);

    expect(container.textContent).toContain('Mortgage Growth Supervisor');
    expect(container.textContent).toContain('Provenance attached');
    const variant = container.querySelector<HTMLSelectElement>(
      'select[aria-label="Message variant for Supervisor IL refinance campaign"]',
    )!;
    const action = () => container.querySelector<HTMLAnchorElement>(
      'a[aria-label="Open Supervisor IL refinance campaign variant B in Lead Queue"]',
    );

    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, 'value')!.set!;
      setter.call(variant, 'B');
      variant.dispatchEvent(new Event('change', { bubbles: true }));
    });

    await waitUntil(() => action() !== null);
    const url = new URL(action()!.href, 'https://mortgage-intelligence.local');
    expect(url.pathname).toBe('/lead-queue');
    expect(url.searchParams.get('states')).toBe('IL');
    expect(url.searchParams.get('marketing_eligibility')).toBe('Eligible only');
    expect(url.searchParams.get('campaign_id')).toBe(campaignId);
    expect(url.searchParams.get('variant_name')).toBe('B');
  });

  it('keeps the production source free of window.prompt', () => {
    // Belt-and-suspenders source pin: the route module must not reference
    // the blocking dialog API at all (catches a regression that the
    // behavioral mocks above might mask if the call became conditional).
    // happy-dom rewrites import.meta.url to an http:// URL, so resolve
    // from the vitest cwd (the frontend package root) instead.
    const routeSource = readFileSync(
      join(process.cwd(), 'src', 'routes', 'portfolio-builder.tsx'),
      'utf-8',
    );
    const campaignPanelSource = readFileSync(
      join(process.cwd(), 'src', 'routes', 'portfolio-builder.campaign-setup.tsx'),
      'utf-8',
    );
    const source = `${routeSource}\n${campaignPanelSource}`;
    expect(source).not.toContain('window.prompt');
    expect(source).toContain('market the household together');
  });

  it('bans the blocking-dialog class repo-wide via eslint config', () => {
    // Re-audit #4: the source pin above guards ONE file; this pins the
    // repo-wide eslint rule so no future surface can reintroduce
    // prompt/alert/confirm. If the rule is removed, this fails before a
    // regression can land.
    const config = readFileSync(join(process.cwd(), 'eslint.config.js'), 'utf-8');
    expect(config).toContain('no-restricted-globals');
    for (const banned of ['prompt', 'alert', 'confirm']) {
      expect(config).toContain(`name: "${banned}"`);
    }
  });
});
