/**
 * @vitest-environment happy-dom
 *
 * Portfolio Builder's per-actor campaign-setup draft (2026-09-21 audit
 * critic-v3), exercised through the rendered route: the operator's setup
 * fields survive a remount (a link out and back, or a reload in this tab),
 * come back with a "Draft restored" chip and a Reset, and are cleared by a
 * save, by Reset and by the shell's actor-change clear. Only the operator
 * setup subset is ever written, and a page without storage renders as before.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CampaignRecommendationResponse, PortfolioPreview } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.setConfig({ testTimeout: 30_000 });

const apiMocks = vi.hoisted(() => ({
  portfolioPreview: vi.fn(),
  portfolioCreate: vi.fn(),
  campaigns: vi.fn(),
  campaignStatus: vi.fn(),
  salesCampaignPerformance: vi.fn(),
  campaignRecommendation: vi.fn(),
}));

vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
vi.mock('../lib/configOptionsQuery', () => {
  const STABLE = { data: { target_lender_refs: ['All'], target_lender_refs_status: 'live' }, isError: false };
  return { useConfigOptionsQuery: () => STABLE };
});
vi.mock('../components/FootprintProvider', () => {
  const STABLE = { ready: true, usingFallback: false, states: [] };
  return { useFootprint: () => STABLE };
});
vi.mock('../components/AppContext', () => ({
  useApp: () => ({ setDrawer: vi.fn(), showEvidence: true, showConfidence: true, canAccessAdmin: false }),
}));

import PortfolioBuilder from './portfolio-builder';
import { CAMPAIGN_DRAFT_STORAGE_KEY, clearActorScopedBrowserState } from '../lib/actorScopedBrowserState';
import { CAMPAIGN_DRAFT_WRITE_DELAY_MS } from './portfolio-builder.draft';

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

const RECOMMENDATION: CampaignRecommendationResponse = {
  generation_mode: 'supervisor',
  generator_label: 'Mortgage Growth Supervisor',
  performance_status: 'insufficient_sample',
  audience_summary: 'Selected cohort.',
  strategy: 'Use reviewed copy.',
  variants: [
    { variant_name: 'Benefit-led', subject: 'Benefit subject', body: 'Benefit body', hypothesis: 'A', provenance_token: 'token-a' },
    { variant_name: 'Guidance-led', subject: 'Guidance subject', body: 'Guidance body', hypothesis: 'B', provenance_token: 'token-b' },
  ],
  holdout_pct: 12,
  evidence: [],
  warnings: [],
};

const STORED_DRAFT = {
  v: 1,
  setup: {
    holdoutPct: '20',
    startLocal: '10:30',
    endLocal: '15:00',
    budget: '25000',
    emailCost: '0.5',
    smsCost: '0.02',
    mailCost: '1.1',
    marketHouseholdTogether: true,
  },
};

describe('Portfolio Builder campaign draft (critic-v3)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    apiMocks.portfolioPreview.mockResolvedValue(PREVIEW);
    apiMocks.campaigns.mockResolvedValue({ campaigns: [] });
    apiMocks.portfolioCreate.mockResolvedValue({ campaign_id: 'c-1', audit_event_id: 'audit-save' });
    apiMocks.salesCampaignPerformance.mockReturnValue(new Promise(() => undefined));
    apiMocks.campaignRecommendation.mockResolvedValue(RECOMMENDATION);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
    vi.useRealTimers();
  });

  function mount() {
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

  /** A reload in this tab: a new page over the same sessionStorage. */
  function remount() {
    act(() => root.unmount());
    root = createRoot(container);
    queryClient.clear();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mount();
  }

  async function waitUntil(condition: () => boolean, ms = 10_000) {
    const started = Date.now();
    while (!condition()) {
      if (Date.now() - started > ms) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  async function wait(ms: number) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, ms));
    });
  }

  function control(label: string): HTMLInputElement {
    const labelEl = [...container.querySelectorAll<HTMLLabelElement>('label.field__label')].find((node) => node.textContent === label);
    const input = labelEl ? document.getElementById(labelEl.htmlFor) : null;
    if (!(input instanceof HTMLInputElement)) throw new Error(`no control labelled ${label}`);
    return input;
  }

  function type(label: string, value: string) {
    const input = control(label);
    act(() => {
      Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => input.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
  }

  const chip = () => container.querySelector('[data-testid="campaign-draft-restored"]');
  const householdToggle = () => container.querySelector<HTMLButtonElement>('button[aria-label="market the household together"]')!;
  const stored = () => {
    const raw = window.sessionStorage.getItem(CAMPAIGN_DRAFT_STORAGE_KEY);
    return raw === null ? null : (JSON.parse(raw) as { v: number; setup: Record<string, unknown> });
  };
  const saveButton = () => container.querySelector<HTMLButtonElement>('[data-testid="portfolio-save-build"]')!;

  it('restores the operator setup after a reload, with the chip, and counts it as unsaved', async () => {
    window.sessionStorage.setItem(CAMPAIGN_DRAFT_STORAGE_KEY, JSON.stringify(STORED_DRAFT));
    const beforeUnload = new Event('beforeunload', { cancelable: true });
    mount();
    expect(chip()?.textContent).toContain('Draft restored');
    expect(control('Holdout % (0-50)').value).toBe('20');
    expect(control('Send start').value).toBe('10:30');
    expect(control('Send end').value).toBe('15:00');
    expect(control('Budget').value).toBe('25000');
    expect(control('Email cost').value).toBe('0.5');
    expect(control('SMS cost').value).toBe('0.02');
    expect(control('Mail cost').value).toBe('1.1');
    expect(householdToggle().getAttribute('aria-pressed')).toBe('true');
    // states-05: a restored draft is unsaved work.
    window.dispatchEvent(beforeUnload);
    expect(beforeUnload.defaultPrevented).toBe(true);
  });

  it('Reset restores the saved setup, clears the key and drops the chip', async () => {
    window.sessionStorage.setItem(CAMPAIGN_DRAFT_STORAGE_KEY, JSON.stringify(STORED_DRAFT));
    mount();
    const reset = [...container.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent === 'Reset')!;
    expect(reset.getAttribute('aria-label')).toBe('Reset restored draft');
    act(() => reset.click());
    expect(chip()).toBeNull();
    expect(control('Holdout % (0-50)').value).toBe('10');
    expect(control('Budget').value).toBe('');
    expect(householdToggle().getAttribute('aria-pressed')).toBe('false');
    expect(stored()).toBeNull();
    await wait(CAMPAIGN_DRAFT_WRITE_DELAY_MS + 100);
    expect(stored()).toBeNull();
    remount();
    expect(chip()).toBeNull();
  });

  it('writes after a 300 ms pause, only while the setup differs from the saved one', async () => {
    mount();
    await waitUntil(() => !saveButton().disabled);
    type('Budget', '5000');
    expect(stored()).toBeNull();
    await wait(CAMPAIGN_DRAFT_WRITE_DELAY_MS / 3);
    expect(stored()).toBeNull();
    await waitUntil(() => stored() !== null);
    expect(stored()?.setup.budget).toBe('5000');
    // Back to the saved (default) value: the key goes away.
    type('Budget', '');
    await waitUntil(() => stored() === null);
  });

  it('persists the operator setup subset only: never the server copy or its provenance', async () => {
    mount();
    await waitUntil(() => [...container.querySelectorAll<HTMLButtonElement>('button')]
      .some((button) => button.textContent?.includes('Apply variants') && !button.disabled));
    const apply = [...container.querySelectorAll<HTMLButtonElement>('button')].find((button) => button.textContent?.includes('Apply variants'))!;
    act(() => apply.click());
    type('Mail cost', '2');
    await waitUntil(() => stored() !== null);
    const draft = stored()!;
    expect(draft.v).toBe(1);
    expect(Object.keys(draft.setup).sort()).toEqual([
      'budget', 'emailCost', 'endLocal', 'holdoutPct', 'mailCost', 'marketHouseholdTogether', 'smsCost', 'startLocal',
    ]);
    expect(draft.setup.holdoutPct).toBe('12');
    const raw = window.sessionStorage.getItem(CAMPAIGN_DRAFT_STORAGE_KEY) ?? '';
    for (const leaked of ['Benefit subject', 'Benefit body', 'token-a', 'supervisor', 'B-']) {
      expect(raw).not.toContain(leaked);
    }
  });

  it('a successful save clears the draft', async () => {
    window.sessionStorage.setItem(CAMPAIGN_DRAFT_STORAGE_KEY, JSON.stringify(STORED_DRAFT));
    mount();
    await waitUntil(() => !saveButton().disabled);
    act(() => saveButton().click());
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="portfolio-save-confirm"]')!.click());
    await waitUntil(() => apiMocks.portfolioCreate.mock.calls.length === 1);
    await waitUntil(() => container.querySelector('[data-testid="portfolio-save-name"]') === null);
    expect(stored()).toBeNull();
    expect(chip()).toBeNull();
    // The saved values stay on screen and are the new baseline.
    expect(control('Budget').value).toBe('25000');
    await wait(CAMPAIGN_DRAFT_WRITE_DELAY_MS + 100);
    expect(stored()).toBeNull();
    remount();
    expect(chip()).toBeNull();
  });

  it("the shell's actor-change clear removes the draft, so the next actor restores nothing", async () => {
    mount();
    await waitUntil(() => !saveButton().disabled);
    type('Budget', '7000');
    await waitUntil(() => stored() !== null);
    clearActorScopedBrowserState();
    expect(stored()).toBeNull();
    // Leaving the page does not bring the cleared draft back.
    act(() => root.unmount());
    root = createRoot(container);
    expect(stored()).toBeNull();
    mount();
    expect(chip()).toBeNull();
    expect(control('Budget').value).toBe('');
  });

  it('flushes a write still waiting when the page goes away', async () => {
    mount();
    await waitUntil(() => !saveButton().disabled);
    type('SMS cost', '0.07');
    expect(stored()).toBeNull();
    remount();
    expect(chip()).not.toBeNull();
    expect(control('SMS cost').value).toBe('0.07');
  });

  it('ignores a malformed stored field and keeps the valid ones, re-normalized', async () => {
    window.sessionStorage.setItem(CAMPAIGN_DRAFT_STORAGE_KEY, JSON.stringify({
      v: 1,
      setup: { holdoutPct: '95', budget: 'lots', startLocal: '25:99', endLocal: '14:00', emailCost: 3, marketHouseholdTogether: 'yes', subjectA: 'Injected' },
    }));
    mount();
    expect(chip()).not.toBeNull();
    expect(control('Holdout % (0-50)').value).toBe('50');
    expect(control('Budget').value).toBe('');
    expect(control('Send start').value).toBe('09:00');
    expect(control('Send end').value).toBe('14:00');
    expect(control('Email cost').value).toBe('');
    expect(householdToggle().getAttribute('aria-pressed')).toBe('false');
    expect(container.textContent).not.toContain('Injected');
  });

  it('renders exactly as before when storage throws', async () => {
    vi.spyOn(Storage.prototype, 'getItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    vi.spyOn(Storage.prototype, 'setItem').mockImplementation(() => {
      throw new Error('QuotaExceededError');
    });
    vi.spyOn(Storage.prototype, 'removeItem').mockImplementation(() => {
      throw new Error('SecurityError');
    });
    mount();
    await waitUntil(() => !saveButton().disabled);
    expect(chip()).toBeNull();
    expect(control('Holdout % (0-50)').value).toBe('10');
    type('Budget', '9000');
    await wait(CAMPAIGN_DRAFT_WRITE_DELAY_MS + 100);
    expect(control('Budget').value).toBe('9000');
    act(() => root.unmount());
    root = createRoot(container);
  });
});
