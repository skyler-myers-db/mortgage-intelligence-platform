/**
 * @vitest-environment happy-dom
 *
 * The campaign-setup draft (2026-09-21 audit critic-v3), on the mounted route:
 * an unsaved setup is kept as this tab's actor-owned draft and restored on
 * return ('Draft restored' and Reset); another build restores the delivery
 * settings only; nothing is written or removed before the restore check (a
 * removal queued while the actor gate is pending would replay before
 * 'opened' and delete the draft); a save, Reset, an actor change and a
 * corrupt value each remove it.
 *
 * Actor tests use the new-document model: the gate is reset to 'pending' for
 * A, and the first observation is B (never a second observation after a real
 * open, which w5-identity-reset turns into a document reset).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { CampaignRecommendationResponse, PortfolioPreview } from '../types';
import {
  NOBODY,
  _resetActorScopeForTests,
  observeActor,
  readActorScoped,
  removeActorScoped,
  writeActorScoped,
} from '../lib/actorScope';
import { BASE_DEFAULT_FILTERS, DEFAULT_CAMPAIGN_SETUP, buildPreviewCriteria, type CampaignSetupState } from './portfolio-builder.logic';
import { CAMPAIGN_DRAFT_KEY, parseCampaignDraft } from './portfolio-builder.draft';

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

const ACTOR_A = 'actor_aaaaaaaaaaaaaaaa';
const ACTOR_B = 'actor_bbbbbbbbbbbbbbbb';
/** The default build's criteria: what `/portfolio-builder` commits on mount. */
const DEFAULT_BUILD_KEY = JSON.stringify(buildPreviewCriteria({ ...BASE_DEFAULT_FILTERS }, []));
const TOKEN_A = 'signed-benefit-provenance-token-000000000000000000000001';
const TOKEN_B = 'signed-guidance-provenance-token-00000000000000000000001';

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
  generator_label: 'Supervisor-generated recommendation',
  performance_status: 'insufficient_sample',
  audience_summary: 'Prime refinance candidates.',
  strategy: 'Use reviewed benefit and guidance variants.',
  variants: [
    { variant_name: 'Benefit-led', subject: 'Recommended benefit subject', body: 'Benefit body', hypothesis: 'H1', provenance_token: TOKEN_A },
    { variant_name: 'Guidance-led', subject: 'Recommended guidance subject', body: 'Guidance body', hypothesis: 'H2', provenance_token: TOKEN_B },
  ],
  holdout_pct: 15,
  evidence: [],
  warnings: [],
};

/** A setup with applied variants and edited delivery settings. */
const EDITED: CampaignSetupState = {
  ...DEFAULT_CAMPAIGN_SETUP,
  subjectA: 'Recommended benefit subject',
  subjectB: 'Recommended guidance subject',
  bodyA: 'Benefit body',
  bodyB: 'Guidance body',
  holdoutPct: '15',
  budget: '25000',
  emailCost: '0.5',
  marketHouseholdTogether: true,
  generationMode: 'supervisor',
  generatorLabel: 'Supervisor-generated recommendation',
  provenanceTokenA: TOKEN_A,
  provenanceTokenB: TOKEN_B,
};

function draftJson(buildKey: string, setup: CampaignSetupState = EDITED): string {
  return JSON.stringify({ v: 1, buildKey, savedAt: 1_790_000_000_000, setup });
}

let container: HTMLDivElement;
let root: Root;

beforeEach(() => {
  for (const mock of [portfolioPreview, portfolioCreate, campaignRecommendation, campaigns, salesCampaignPerformance]) mock.mockReset();
  campaigns.mockResolvedValue({ campaigns: [] });
  portfolioPreview.mockResolvedValue(PREVIEW);
  portfolioCreate.mockResolvedValue({ campaign_id: 'campaign-0001', audit_event_id: 'audit-0001' });
  campaignRecommendation.mockResolvedValue(RECOMMENDATION);
  salesCampaignPerformance.mockResolvedValue({});
  removeActorScoped('session', CAMPAIGN_DRAFT_KEY);
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});

afterEach(() => {
  act(() => root.unmount());
  container.remove();
  _resetActorScopeForTests({ status: 'open', owner: NOBODY });
  removeActorScoped('session', CAMPAIGN_DRAFT_KEY);
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

function remount() {
  act(() => root.unmount());
  root = createRoot(container);
  mount();
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

function control(label: string): HTMLInputElement {
  const labelEl = [...container.querySelectorAll<HTMLLabelElement>('label.field__label')].find((node) => node.textContent === label);
  const input = labelEl ? document.getElementById(labelEl.htmlFor) : null;
  if (!(input instanceof HTMLInputElement)) throw new Error(`no control labelled ${label}`);
  return input;
}

function copyText(label: string): string {
  const field = [...container.querySelectorAll<HTMLElement>('.campaign-setup__field')]
    .find((node) => node.firstElementChild?.textContent === label);
  const value = field?.querySelector<HTMLElement>('.field__value');
  if (!value) throw new Error(`no readout ${label}`);
  return value.classList.contains('field__readout--empty') ? '' : (value.textContent ?? '');
}

function commit(label: string, value: string) {
  const input = control(label);
  act(() => {
    Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, 'value')!.set!.call(input, value);
    input.dispatchEvent(new Event('input', { bubbles: true }));
  });
  act(() => input.dispatchEvent(new FocusEvent('focusout', { bubbles: true })));
}

const chip = () => container.querySelector('[data-testid="campaign-draft-restored"]');
const note = () => container.querySelector('[data-testid="campaign-draft-variants-note"]');
const button = (text: string) => [...container.querySelectorAll<HTMLButtonElement>('button')].find((node) => node.textContent?.trim() === text);
const stored = () => parseCampaignDraft(readActorScoped('session', CAMPAIGN_DRAFT_KEY));
const household = () => container.querySelector<HTMLButtonElement>('button[aria-label="market the household together"]')!;

describe('the campaign-setup draft (critic-v3)', () => {
  it('keeps an edited setup and its applied variants across a remount, restoring the whole setup for the same build', async () => {
    mount();
    expect(chip()).toBeNull();
    await waitUntil(() => button('Apply variants')?.disabled === false);
    act(() => button('Apply variants')!.click());
    commit('Budget', '25000');
    expect(stored()?.buildKey).toBe(DEFAULT_BUILD_KEY);
    expect(stored()?.setup).toMatchObject({ budget: '25000', subjectA: 'Recommended benefit subject', provenanceTokenA: TOKEN_A });

    remount();
    expect(chip()?.textContent).toBe('Draft restored');
    expect(button('Reset')).toBeDefined();
    expect(note()).toBeNull();
    expect(control('Budget').value).toBe('25000');
    expect(control('Holdout % (0-50)').value).toBe('15');
    expect(copyText('Benefit-led subject')).toBe('Recommended benefit subject');
    expect(copyText('Guidance-led message')).toBe('Guidance body');
  });

  it("restores only the delivery settings from another build's draft, and says the variants were left out", () => {
    writeActorScoped('session', CAMPAIGN_DRAFT_KEY, draftJson('{"another":"build"}'));
    mount();
    expect(chip()?.textContent).toBe('Draft restored');
    expect(note()?.textContent).toBe('Message variants were for a different build and were not restored.');
    expect(control('Budget').value).toBe('25000');
    expect(control('Email cost').value).toBe('0.5');
    expect(control('Holdout % (0-50)').value).toBe('15');
    expect(household().getAttribute('aria-pressed')).toBe('true');
    expect(copyText('Benefit-led subject')).toBe('');
    expect(copyText('Guidance-led message')).toBe('');
    // What is written back carries this build and none of the other build's copy.
    expect(stored()?.buildKey).toBe(DEFAULT_BUILD_KEY);
    expect(stored()?.setup).toMatchObject({ subjectA: '', provenanceTokenA: null, generationMode: 'operator', budget: '25000' });
  });

  it('a route mounted while the gate is pending writes and removes nothing, and restores once the gate opens', () => {
    writeActorScoped('session', CAMPAIGN_DRAFT_KEY, draftJson(DEFAULT_BUILD_KEY));
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    mount();
    expect(chip()).toBeNull();
    expect(control('Budget').value).toBe('');

    act(() => observeActor({ key: ACTOR_A }));
    // A removal queued while pending would have replayed before 'opened' and
    // deleted the draft this restore reads.
    expect(chip()?.textContent).toBe('Draft restored');
    expect(control('Budget').value).toBe('25000');
    expect(stored()?.setup.budget).toBe('25000');
  });

  it('an edit made while the gate is pending wins: no restore, and the draft then holds the edit', () => {
    writeActorScoped('session', CAMPAIGN_DRAFT_KEY, draftJson(DEFAULT_BUILD_KEY));
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    mount();
    commit('Mail cost', '1.25');

    act(() => observeActor({ key: ACTOR_A }));
    expect(chip()).toBeNull();
    expect(control('Mail cost').value).toBe('1.25');
    expect(control('Budget').value).toBe('');
    expect(stored()?.setup).toMatchObject({ mailCost: '1.25', budget: '' });
  });

  it("another actor's first observation removes the draft: nothing is restored", () => {
    writeActorScoped('session', CAMPAIGN_DRAFT_KEY, draftJson(DEFAULT_BUILD_KEY));
    _resetActorScopeForTests({ status: 'pending', owner: ACTOR_A });
    mount();

    act(() => observeActor({ key: ACTOR_B }));
    expect(chip()).toBeNull();
    expect(control('Budget').value).toBe('');
    expect(readActorScoped('session', CAMPAIGN_DRAFT_KEY)).toBeNull();
  });

  it('Reset returns to the defaults, removes the draft, hides the chip and puts focus on Holdout', () => {
    writeActorScoped('session', CAMPAIGN_DRAFT_KEY, draftJson(DEFAULT_BUILD_KEY));
    mount();
    expect(chip()).not.toBeNull();
    button('Reset')!.focus();
    act(() => button('Reset')!.click());
    expect(chip()).toBeNull();
    expect(button('Reset')).toBeUndefined();
    expect(readActorScoped('session', CAMPAIGN_DRAFT_KEY)).toBeNull();
    expect(control('Budget').value).toBe('');
    expect(control('Holdout % (0-50)').value).toBe('10');
    expect(document.activeElement).toBe(control('Holdout % (0-50)'));
  });

  it('a successful save removes the draft, and a remount restores nothing', async () => {
    mount();
    commit('Budget', '30000');
    expect(stored()?.setup.budget).toBe('30000');
    await waitUntil(() => container.querySelector<HTMLButtonElement>('[data-testid="portfolio-save-build"]')?.disabled === false);
    act(() => container.querySelector<HTMLButtonElement>('[data-testid="portfolio-save-build"]')!.click());
    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="portfolio-save-confirm"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 20));
    });
    await waitUntil(() => container.querySelector('[data-testid="portfolio-save-name"]') === null);
    expect(portfolioCreate).toHaveBeenCalledTimes(1);
    expect(readActorScoped('session', CAMPAIGN_DRAFT_KEY)).toBeNull();

    remount();
    expect(chip()).toBeNull();
    expect(control('Budget').value).toBe('');
  });

  it('a setup typed back to the baseline removes the draft', () => {
    mount();
    commit('Budget', '30000');
    expect(stored()).not.toBeNull();
    commit('Budget', '');
    expect(readActorScoped('session', CAMPAIGN_DRAFT_KEY)).toBeNull();
  });

  for (const [name, raw] of [
    ['corrupt JSON', '{"v":1,"buildKey":'],
    ['an unknown version', JSON.stringify({ ...JSON.parse(draftJson(DEFAULT_BUILD_KEY)), v: 2 })],
    ['a generation mode outside the enum', draftJson(DEFAULT_BUILD_KEY, { ...EDITED, generationMode: 'rogue' as CampaignSetupState['generationMode'] })],
    ['a provenance token too short to be one', draftJson(DEFAULT_BUILD_KEY, { ...EDITED, provenanceTokenA: 'short' })],
    ['a non-string budget', draftJson(DEFAULT_BUILD_KEY).replace('"budget":"25000"', '"budget":25000')],
  ] as const) {
    it(`removes ${name} and restores nothing`, () => {
      writeActorScoped('session', CAMPAIGN_DRAFT_KEY, raw);
      mount();
      expect(chip()).toBeNull();
      expect(control('Budget').value).toBe('');
      expect(readActorScoped('session', CAMPAIGN_DRAFT_KEY)).toBeNull();
    });
  }
});
