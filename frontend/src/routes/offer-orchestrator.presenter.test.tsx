/**
 * @vitest-environment happy-dom
 *
 * The PROTOTYPE borrower view is a demo affordance (D-shell-deviations-e1,
 * audit critic-05): an approver used to find a watermarked Module 1 mock
 * beside Approve. It now shows only in presenter mode, and its module is not
 * even downloaded otherwise; a pending or failed session check reads as off.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Borrower360, BorrowerLifecycle, OfferRecommendation, SessionResponse } from '../types';

const apiMocks = vi.hoisted(() => ({
  borrower: vi.fn(),
  recommendOffer: vi.fn(),
  borrowerLifecycle: vi.fn(),
  draftOutreach: vi.fn(),
  salesTeam: vi.fn(),
  session: vi.fn(),
}));

/** How many times the mock's module was evaluated (it is, once, on its first import). */
const previewModule = vi.hoisted(() => ({ loads: 0 }));

// The audit-free decision history (D-audit-reads-c2) stays pending: this suite counts its own reads.
vi.mock('../lib/apiClients/borrowerDecisions', () => ({ borrowerDecisionsQuery: (id: string) => ({ queryKey: ['mip', 'borrower', id, 'decisions'], queryFn: () => new Promise(() => undefined) }) }));
vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status: number | null;

    constructor(message: string, opts: { path: string; status?: number | null } = { path: '' }) {
      super(message);
      this.status = opts.status ?? null;
    }
  }
  return {
    api: apiMocks,
    ApiError,
    isAbortError: () => false,
    isWarmingUpError: () => false,
    dependencyLabel: () => 'Dependency',
  };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    lastBorrowerId: null,
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    savedDrafts: {},
    saveDraft: vi.fn(),
    removeSavedDraft: vi.fn(),
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canAccessAdmin: false,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
  }),
}));

vi.mock('../components/activation/ActivationLoopPanel', () => ({ ActivationLoopPanel: () => null }));

vi.mock('../components/mortgage/BorrowerOfferPreviewMock', () => {
  previewModule.loads += 1;
  return {
    BorrowerOfferPreviewMock: ({ onClose }: { onClose: () => void }) => (
      <div data-testid="borrower-preview-mock">
        PROTOTYPE borrower view
        <button type="button" onClick={onClose}>Close preview</button>
      </div>
    ),
  };
});

import OfferOrchestrator from './offer-orchestrator';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BORROWER_ID = 'B-0000000000001';

const BORROWER = {
  borrower_id: BORROWER_ID,
  source_refreshed_at: '2026-07-13T12:00:00Z',
  city: 'Chicago',
  state: 'IL',
  zip: '60601',
  segment_codes: ['itm'],
  opportunity_score: 91,
  confidence: 0.94,
  recommended_offer_code: 'rate_term_refi',
  recommended_offer: 'Rate and term refinance review',
  why_now: 'Current lien rate exceeds the reviewed market threshold.',
  evidence_ids: ['ev-1'],
  approval_status: 'pending',
  trigger_timeline: [],
  evidence_events: [],
} as unknown as Borrower360;

const RECOMMENDATION = {
  borrower_id: BORROWER_ID,
  source_refreshed_at: '2026-07-13T12:00:00Z',
  offer_code: 'rate_term_refi',
  offer_type: 'refi',
  product_label: 'Rate and term refinance review',
  confidence: 0.94,
  rationale: 'Reviewed lien economics support a refinance conversation.',
  evidence_ids: ['ev-1'],
  sources: ['mip.gold.borrower_360'],
  alternatives: [],
  thresholds_applied: {},
} as unknown as OfferRecommendation;

const LIFECYCLE: BorrowerLifecycle = { borrower_id: BORROWER_ID, approval_status: 'pending', outreach_status: 'none' };

const SESSION = (presenter: boolean): SessionResponse => ({
  can_access_admin: false,
  can_approve: true,
  can_read_audit: false,
  presenter_mode: presenter,
});

describe('Offer Orchestrator presenter-only borrower preview', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    apiMocks.borrower.mockReset().mockResolvedValue(BORROWER);
    apiMocks.recommendOffer.mockReset().mockResolvedValue(RECOMMENDATION);
    apiMocks.borrowerLifecycle.mockReset().mockResolvedValue(LIFECYCLE);
    apiMocks.draftOutreach.mockReset().mockReturnValue(new Promise(() => undefined));
    apiMocks.salesTeam.mockReset().mockResolvedValue([]);
    apiMocks.session.mockReset().mockResolvedValue(SESSION(false));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
  });

  async function settle(rounds = 20): Promise<void> {
    for (let round = 0; round < rounds; round += 1) {
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 5));
      });
    }
  }

  async function mount(): Promise<void> {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/offer-orchestrator/${BORROWER_ID}`]}>
            <Routes>
              <Route path="/offer-orchestrator/:id" element={<OfferOrchestrator />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    await settle();
  }

  const previewButton = () => container.querySelector<HTMLButtonElement>('[data-testid="preview-borrower-offer"]');
  const heroLoaded = () => container.querySelector('.proto-hero')?.textContent?.includes('91') ?? false;

  // Order matters: the module registry is shared across this file, so the
  // "never imported" cases run before the one that loads it.
  it('shows no preview button and never imports the mock in customer mode', async () => {
    await mount();

    expect(heroLoaded()).toBe(true);
    expect(previewButton()).toBeNull();
    expect(previewModule.loads).toBe(0);
  });

  it.each([
    ['pending', () => new Promise<SessionResponse>(() => undefined)],
    ['errored', () => Promise.reject(new Error('session down'))],
  ])('behaves as customer mode while the session check is %s', async (_state, reply) => {
    apiMocks.session.mockImplementation(reply);
    await mount();

    expect(heroLoaded()).toBe(true);
    expect(previewButton()).toBeNull();
    expect(previewModule.loads).toBe(0);
  });

  it('in presenter mode offers the button, loads the mock on open and closes it', async () => {
    apiMocks.session.mockResolvedValue(SESSION(true));
    await mount();

    expect(previewButton()?.textContent).toContain('Preview borrower view');
    expect(previewModule.loads).toBe(0);

    await act(async () => {
      previewButton()?.click();
    });
    await settle();

    expect(previewModule.loads).toBe(1);
    const mock = container.querySelector('[data-testid="borrower-preview-mock"]');
    expect(mock?.textContent).toContain('PROTOTYPE borrower view');

    await act(async () => {
      mock?.querySelector('button')?.click();
    });
    expect(container.querySelector('[data-testid="borrower-preview-mock"]')).toBeNull();
  });
});
