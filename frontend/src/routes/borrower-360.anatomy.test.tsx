/**
 * @vitest-environment happy-dom
 *
 * Borrower 360 mounts Score anatomy in the Primary offer card (wow-stage-2),
 * rendered at the ROUTE layer: the dossier's natural load never reads
 * /proof (an audited VIEW_BORROWER_PROOF); the disclosure reads it once; a
 * spine segment opens the page's one proof drawer on the Math tab with that
 * component's card focused, over the cache; "Show math" still opens it with
 * the close button focused; and a proof "Show math" already read renders the
 * anatomy expanded without a second read.
 *
 * Mutation check (lane report): enabling the gate's cache observer
 * (ScoreAnatomyGate.tsx `useBorrowerProof(borrowerId, false)` -> `true`)
 * fails "the natural load reads no proof".
 */
import { act, type ReactNode } from 'react';
import type { QueryClient } from '@tanstack/react-query';
import { QueryClientProvider } from '@tanstack/react-query';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Borrower360 as Borrower360Type } from '../types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** The first import of the Score anatomy chunk transforms its module graph: allow for a loaded machine. */
const WAIT = { timeout: 15_000 };
vi.setConfig({ testTimeout: 30_000 });

const state = vi.hoisted(() => ({ borrower: null as unknown }));
const apiMocks = vi.hoisted(() => ({
  borrower: vi.fn(),
  borrowerLifecycle: vi.fn(),
  borrowerProof: vi.fn(),
}));

vi.mock('../lib/useWarmingUpRetry', () => ({
  useWarmingUpRetry: () => ({ data: state.borrower, warmingUp: null, error: null, manualRetry: vi.fn() }),
}));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status = 500;
  }
  return { api: apiMocks, ApiError, isWarmingUpError: () => false };
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
vi.mock('../components/mortgage/TopLeadsQuickPick', () => ({ TopLeadsQuickPick: () => null }));
vi.mock('../components/fx/Reveal', () => ({
  Reveal: ({ children }: { children?: ReactNode }) => <>{children}</>,
}));
vi.mock('../components/GlossaryTerm', () => ({
  GlossaryTerm: ({ children, term }: { children?: ReactNode; term?: string }) => <>{children ?? term}</>,
}));

import Borrower360 from './borrower-360';
import { createMipQueryClient } from '../lib/queryClient';
import { queryKeys } from '../lib/queryKeys';
import { sampleProof } from '../mocks/scoreAnatomyProof';

const BORROWER_ID = 'B-1ABCDEFGHIJK2';

function borrower(): Borrower360Type {
  return {
    borrower_id: BORROWER_ID,
    city: 'Chicago', state: 'IL', zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 285_000,
    rate_spread_bps: 88,
    opportunity_score: 90,
    confidence: 87,
    recommended_offer: 'Refinance + HELOC',
    recommended_offer_code: 'refi_plus_heloc',
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
    trigger_timeline: [],
    evidence_events: [],
    why_panel: {
      in_the_money: true,
      in_the_money_reason: 'Spread and equity both clear the screen.',
      rate_spread_bps: 88,
      market_rate: 0.04875,
      sources: [],
    },
  } as unknown as Borrower360Type;
}

async function flush(rounds = 8) {
  for (let i = 0; i < rounds; i += 1) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }
}

describe('Borrower 360 score anatomy', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    client = createMipQueryClient();
    client.setQueryData(queryKeys.borrowerLifecycle(BORROWER_ID), {
      borrower_id: BORROWER_ID, approval_status: 'pending', outreach_status: 'none', audit_event_id: null,
    });
    apiMocks.borrowerProof.mockImplementation(async (id: string) => sampleProof({ borrower_id: id }));
    state.borrower = borrower();
  });

  afterEach(() => {
    act(() => root.unmount());
    client.clear();
    container.remove();
    vi.clearAllMocks();
    document.querySelectorAll('.drawer-scrim, .proof-drawer').forEach((node) => node.remove());
  });

  async function mount() {
    await act(async () => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[`/borrower-360/${BORROWER_ID}`]}>
            <Routes>
              <Route path="/borrower-360/:id" element={<Borrower360 />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await flush();
  }

  const button = (name: string) =>
    [...document.querySelectorAll<HTMLButtonElement>('button')].find((item) => item.textContent === name) ?? null;
  const drawer = () => document.querySelector('.proof-drawer');

  async function click(element: HTMLElement | null) {
    await act(async () => {
      element?.click();
    });
    await flush();
  }

  it('the natural load reads no proof; the disclosure sits in the Primary offer card', async () => {
    await mount();
    expect(apiMocks.borrowerProof).not.toHaveBeenCalled();
    const gate = container.querySelector('[data-testid="score-anatomy-spine"]');
    const card = gate?.closest('.surface');
    expect(card?.textContent).toContain('Build outreach draft');
    expect(card?.textContent).toContain('Show math');
    expect(button('Score anatomy')?.getAttribute('aria-expanded')).toBe('false');
    // Navigating away and back is a remount: still nothing.
    act(() => root.unmount());
    root = createRoot(container);
    await mount();
    expect(apiMocks.borrowerProof).not.toHaveBeenCalled();
  });

  it('a segment opens the drawer on the Math tab with its card focused, over the cache', async () => {
    await mount();
    await click(button('Score anatomy'));
    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine"]')).not.toBeNull(), WAIT);
    expect(apiMocks.borrowerProof).toHaveBeenCalledTimes(1);

    // Park the drawer on another tab first: a segment must still land on Math.
    await click(button('Show math'));
    // The drawer is its own chunk now: it mounts once that chunk loads.
    await vi.waitFor(() => expect(drawer()?.classList.contains('is-open')).toBe(true), WAIT);
    await click([...document.querySelectorAll<HTMLButtonElement>('.proof-tab')].find((tab) => tab.textContent === 'Evidence') ?? null);
    await act(async () => {
      window.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
    });
    await flush();
    expect(drawer()?.classList.contains('is-open')).toBe(false);

    await click(container.querySelector<HTMLButtonElement>('button.score-spine__seg[data-part="fit"]'));
    expect(drawer()?.classList.contains('is-open')).toBe(true);
    expect(document.querySelector('.proof-tab.is-active')?.textContent).toBe('Math');
    await vi.waitFor(() => expect(document.activeElement?.getAttribute('data-component-key')).toBe('fit'), WAIT);
    expect(document.activeElement?.classList.contains('proof-component--focused')).toBe(true);
    expect(apiMocks.borrowerProof).toHaveBeenCalledTimes(1);
  });

  it('"Show math" fills the cache, and the anatomy then renders expanded with no second read', async () => {
    await mount();
    await click(button('Show math'));
    await vi.waitFor(() => expect(apiMocks.borrowerProof).toHaveBeenCalledTimes(1), WAIT);
    await vi.waitFor(() => expect(document.activeElement?.getAttribute('aria-label')).toBe('Close proof drawer'), WAIT);
    expect(document.querySelector('.proof-component--focused')).toBeNull();

    await vi.waitFor(() => expect(container.querySelector('[data-testid="score-spine-seal"]')).not.toBeNull(), WAIT);
    expect(button('Score anatomy')?.getAttribute('aria-expanded')).toBe('true');
    expect(apiMocks.borrowerProof).toHaveBeenCalledTimes(1);
  });
});
