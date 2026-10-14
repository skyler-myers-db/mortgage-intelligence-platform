/**
 * @vitest-environment happy-dom
 *
 * Borrower 360 route-level pins for this lane's dossier surfaces, mounted
 * through the ROUTE (the defects lived in its JSX):
 *  - critic-08: the synthetic-data note and the Metro / loan type raw pair
 *    are ui/Tooltip descriptions, never DOM `title=` attributes;
 *  - wow-power-2 (item 4d): "More like this" on the Primary offer card reads
 *    nothing until clicked, then shows the pattern panel with one audit-free
 *    count and links only (no Triage deck link for a non-approver).
 */
import { act } from 'react';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
vi.setConfig({ testTimeout: 30_000 });
import type { ReactNode } from 'react';
import type { Borrower360 as Borrower360Type } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const state = vi.hoisted(() => ({ borrower: null as unknown }));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({
    data: state.borrower,
    warmingUp: null,
    error: null,
    manualRetry: vi.fn(),
  }),
}));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status = 500;
  }
  return { api: { borrower: vi.fn(), borrowerLifecycle: vi.fn() }, ApiError };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    lastBorrowerId: null,
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
  }),
}));

vi.mock('../components/layout/PageShell', () => ({
  PageShell: ({ children }: { children?: ReactNode }) => <main>{children}</main>,
}));
vi.mock('../components/mortgage/BorrowerStoryCard', () => ({ BorrowerStoryCard: () => null }));
vi.mock('../components/mortgage/TriggerTimeline', () => ({ TriggerTimeline: () => null }));
vi.mock('../components/mortgage/BorrowerProofDrawer', () => ({ BorrowerProofDrawer: () => null }));
vi.mock('../components/mortgage/BorrowerTruthFlags', () => ({ BorrowerTruthFlags: () => null }));
vi.mock('../components/mortgage/TopLeadsQuickPick', () => ({ TopLeadsQuickPick: () => null }));
vi.mock('../components/mortgage/ScoreBadge', () => ({ ScoreBadge: () => null }));
vi.mock('../components/mortgage/ConfidenceMeter', () => ({ ConfidenceMeter: () => null }));
vi.mock('../components/fx/Reveal', () => ({
  Reveal: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));
vi.mock('../components/GlossaryTerm', () => ({
  GlossaryTerm: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));

import Borrower360 from './borrower-360';
import { queryKeys } from '../lib/queryKeys';

const BORROWER_ID = 'B-1ABCDEFGHIJK2';

function borrower(overrides: Partial<Borrower360Type> = {}): Borrower360Type {
  return {
    borrower_id: BORROWER_ID,
    city: 'Chicago', state: 'IL', zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 285_000,
    rate_spread_bps: 88,
    opportunity_score: 82,
    confidence: 80,
    recommended_offer: 'Refinance',
    recommended_offer_code: 'refi',
    why_now: 'Rate spread clears the bar.',
    is_investor: false,
    related_property_count: 1,
    clip_id: 'clip_demo_48291',
    owner_link_id: 'ol_demo_48291',
    subject_property: 'Chicago, IL 60611',
    avm_value: 625_000,
    current_lien_balance: 340_000,
    current_rate: 5.75,
    ltv: 54,
    ltv_basis_is_unreliable: false,
    situs_cbsa_code: '16980',
    first_pos_loan_type: 'CNV',
    first_party_synthetic_demo: true,
    trigger_timeline: [],
    evidence_events: [],
    why_panel: {
      in_the_money: true,
      in_the_money_reason: 'Spread and equity both clear the screen.',
      rate_spread_bps: 88,
      market_rate: 0.0487,
      sources: [],
    },
    ...overrides,
  } as unknown as Borrower360Type;
}

let container: HTMLDivElement;
let root: Root;
let calls: string[] = [];

beforeEach(() => {
  calls = [];
  vi.stubGlobal('fetch', async (path: string) => {
    calls.push(path);
    return new Response(JSON.stringify({ total_matching: 9 }), { status: 200, headers: { 'Content-Type': 'application/json' } });
  });
  container = document.createElement('div');
  document.body.appendChild(container);
  root = createRoot(container);
});
afterEach(() => {
  act(() => root.unmount());
  container.remove();
  vi.unstubAllGlobals();
});

function mount(b: Borrower360Type) {
  state.borrower = b;
  const queryClient = new QueryClient();
  queryClient.setQueryData(queryKeys.borrowerLifecycle(BORROWER_ID), {
    borrower_id: BORROWER_ID,
    approval_status: 'pending',
    outreach_status: 'none',
    audit_event_id: null,
  });
  act(() => {
    root.render(
      <QueryClientProvider client={queryClient}>
        <MemoryRouter initialEntries={[`/borrower-360/${BORROWER_ID}`]}>
          <Routes>
            <Route path="/borrower-360/:id" element={<Borrower360 />} />
          </Routes>
        </MemoryRouter>
      </QueryClientProvider>,
    );
  });
}

/** The text of every element an element's aria-describedby names. */
function description(element: Element): string {
  return (element.getAttribute('aria-describedby') ?? '')
    .split(' ')
    .map((id) => document.getElementById(id)?.textContent ?? '')
    .join(' ');
}

describe('Borrower 360 dossier tooltips (critic-08)', () => {
  it('describes the synthetic-data note and the raw metro / loan type pair through ui/Tooltip, never title=', () => {
    mount(borrower());
    const note = [...container.querySelectorAll<HTMLElement>('span')]
      .find((span) => span.textContent === 'Summit demo synthetic');
    expect(note).toBeTruthy();
    expect(note!.hasAttribute('title')).toBe(false);
    expect(description(note!)).toContain('synthetic demo data');

    const label = [...container.querySelectorAll<HTMLElement>('.field__label')]
      .find((element) => element.textContent === 'Metro / loan type');
    const value = label!.parentElement!.querySelector<HTMLElement>('.field__value')!;
    expect(value.hasAttribute('title')).toBe(false);
    expect(description(value)).toBe('16980 · CNV');
    // No other field__value carries a native title either.
    expect(container.querySelector('.field__value[title]')).toBeNull();
  });
});

describe('Borrower 360 More like this (wow-power-2)', () => {
  it('reads nothing until clicked, then shows the pattern with one count and links only', async () => {
    mount(borrower());
    const trigger = container.querySelector<HTMLButtonElement>('[data-testid="borrower-more-like-this"]')!;
    expect(trigger.closest('.chip-row')?.textContent).toContain('Build outreach draft');
    expect(calls.filter((path) => path.includes('/leads/count'))).toEqual([]);
    await act(async () => trigger.click());
    await vi.waitFor(async () => {
      await act(async () => {
        for (let i = 0; i < 6; i += 1) await new Promise((resolve) => window.setTimeout(resolve, 0));
      });
      expect(container.querySelector('.similar-cohort [role="status"]')?.textContent).toBe('9 borrowers match this pattern');
    }, { timeout: 20_000 });
    const counts = calls.filter((path) => path.includes('/leads/count'));
    expect(counts).toHaveLength(1);
    expect(new URL(counts[0], 'http://b360.test').searchParams.get('state')).toBe('IL');
    expect(container.querySelector('[data-testid="lead-similar-open-queue"]')?.getAttribute('href')).toContain('/lead-queue?segment=itm');
    // The mocked session cannot approve: no Triage deck link.
    expect(container.querySelector('[data-testid="lead-similar-open-triage"]')).toBeNull();
  });
});
