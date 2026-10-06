/**
 * @vitest-environment happy-dom
 *
 * The Offer's prior-decisions disclosure (audit flow-04 phase 2,
 * D-audit-reads-c2), through the real route: it reads the audit-free
 * decision history once on mount, collapsed; after a RESOLVED approve or
 * reject (never optimistically) it re-reads that exact key once, and the
 * audited dossier and snapshot reads are never repeated for it.
 *
 * The Offer reject has no default reason (D-approval-flow-d item 13, the
 * Offer half; tables-07 / states-08): the Reason opens on "Choose a reason",
 * Confirm reject stays aria-disabled (never natively disabled) until one is
 * chosen, and a submit without one focuses Reason and sends nothing.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Borrower360, BorrowerLifecycle, OfferRecommendation } from '../types';
import type { OutreachDraftResult } from '../lib/apiTypes';
import type { BorrowerDecisionHistoryResponse } from '../lib/apiClients/borrowerDecisions';

const apiMocks = vi.hoisted(() => ({
  borrower: vi.fn(),
  recommendOffer: vi.fn(),
  borrowerLifecycle: vi.fn(),
  draftOutreach: vi.fn(),
  salesTeam: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  auditReceipt: vi.fn(),
}));
const decisions = vi.hoisted(() => ({ list: vi.fn() }));
const appMocks = vi.hoisted(() => ({ approvals: {} as Record<string, 'approved' | 'rejected'> }));

// Mocked synchronously: the history chunk is imported lazily and must see this.
vi.mock('../lib/apiClients/borrowerDecisions', () => ({
  borrowerDecisionsQuery: (id: string) => ({
    queryKey: ['mip', 'borrower', id, 'decisions'],
    queryFn: () => decisions.list(id),
    staleTime: 30_000,
    retry: false,
  }),
}));
vi.mock('../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../lib/api')>()),
  api: apiMocks,
}));
vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    approvals: appMocks.approvals,
    setApproval: (borrowerId: string, status: 'approved' | 'rejected') => {
      appMocks.approvals[borrowerId] = status;
    },
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

import OfferOrchestrator from './offer-orchestrator';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.setConfig({ testTimeout: 30_000 });

const ID = 'B-0000000000001';
const SNAPSHOT = '2026-07-13T12:00:00Z';

const BORROWER = {
  borrower_id: ID, source_refreshed_at: SNAPSHOT, display_name: 'Owner 1', city: 'Chicago', state: 'IL',
  zip: '60601', clip: 'CLIP-0001', segment_codes: ['itm'], equity_estimate: 180_000, rate_spread_bps: 125,
  opportunity_score: 91, confidence: 0.94, recommended_offer_code: 'rate_term_refi',
  recommended_offer: 'Rate and term refinance review', why_now: 'Current lien rate exceeds the reviewed market threshold.',
  evidence_ids: ['ev-1'], approval_status: 'pending', clip_id: 'clip-1', owner_link_id: 'owner-1',
  subject_property: '100 Main St', avm_value: 500_000, current_lien_balance: 320_000, current_rate: 7.25, ltv: 0.64,
  related_property_count: 1, trigger_timeline: [], evidence_events: [],
  why_panel: {
    rate_spread_bps: 125, market_rate: 6, equity_pct: 36, in_the_money: true,
    in_the_money_reason: 'Reviewed rate and equity thresholds pass.', min_spread_bps: 75, min_equity_pct: 15,
    sources: ['mip.gold.borrower_360'],
  },
} as Borrower360;

const RECOMMENDATION: OfferRecommendation = {
  borrower_id: ID, source_refreshed_at: SNAPSHOT, offer_code: 'rate_term_refi', offer_type: 'refi',
  product_label: 'Rate and term refinance review', confidence: 0.94,
  rationale: 'Reviewed lien economics support a refinance conversation.', evidence_ids: ['ev-1', 'ev-2'],
  sources: ['mip.gold.borrower_360'], alternatives: [], thresholds_applied: { min_spread_bps: 75, min_equity_pct: 15 },
};

const LIFECYCLE: BorrowerLifecycle = { borrower_id: ID, approval_status: 'pending', outreach_status: 'none' };

const DRAFT: OutreachDraftResult = {
  generation_id: 'gen-email', response_hash: 'a'.repeat(64), source_refreshed_at: SNAPSHOT, borrower_id: ID,
  campaign_id: null, variant_name: null, offer_code: 'rate_term_refi', channel: 'email', subject: 'email subject',
  body: 'email governed body', status: 'draft', disclosure_version: 'v1', disclosure_state: 'IL',
  marketing_eligible: true, generation_mode: 'supervisor', generator_label: 'Mortgage Growth Supervisor',
  strategy_summary: 'Use a clear, low-pressure review invitation.',
  evidence_summary: ['Rate spread passes the reviewed threshold.'], evidence_assets: ['mip.gold.borrower_360'],
};

const HISTORY: BorrowerDecisionHistoryResponse = {
  borrower_id: ID,
  truncated: false,
  items: [{
    audit_event_id: 'audit-colleague-reject', event_type: 'OUTREACH_REJECT', outcome: 'rejected',
    occurred_at: '2026-09-01T12:00:00Z', actor_display: 'Summit LO 02 (Loan officer)', actor_kind: 'staff',
    is_own: false, offer_code: 'heloc', channel: 'email', rationale_label: 'Compliance review',
    contact_block_label: null, assigned_to_display: null, from_status: null, to_status: null,
    disposition_outcome: null, lead_outcome_type: null, activation_status: null, bulk: false, receipt_available: false,
  }],
};

describe('Offer prior decisions', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    appMocks.approvals = {};
    apiMocks.borrower.mockResolvedValue(BORROWER);
    apiMocks.recommendOffer.mockResolvedValue(RECOMMENDATION);
    apiMocks.borrowerLifecycle.mockResolvedValue(LIFECYCLE);
    apiMocks.draftOutreach.mockResolvedValue(DRAFT);
    apiMocks.salesTeam.mockResolvedValue([]);
    apiMocks.approve.mockResolvedValue({ approved: true, audit_event_id: 'audit-1', approval_id: 'approval-1' });
    apiMocks.reject.mockResolvedValue({ rejected: true, audit_event_id: 'audit-reject-1' });
    apiMocks.auditReceipt.mockReturnValue(new Promise(() => undefined));
    decisions.list.mockResolvedValue(HISTORY);
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
  });

  function mount() {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/offer-orchestrator/${ID}`]}>
            <main>
              <Routes>
                <Route path="/offer-orchestrator/:id" element={<OfferOrchestrator />} />
              </Routes>
            </main>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  async function waitUntil(condition: () => boolean, timeoutMs = 10_000) {
    const started = Date.now();
    while (!condition()) {
      if (Date.now() - started > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  const findButton = (text: string) =>
    [...container.querySelectorAll<HTMLButtonElement>('button')].find((candidate) => candidate.textContent?.trim() === text);
  const toggle = () => container.querySelector<HTMLButtonElement>('[data-testid="offer-prior-decisions"] > button');
  const loaded = () => {
    const body = container.querySelector<HTMLElement>('[data-testid="outreach-draft"]');
    return body !== null && body.getAttribute('aria-disabled') !== 'true' && body.textContent === 'email governed body';
  };

  it('reads the history once on mount, collapsed, with the count in its toggle', async () => {
    mount();
    await waitUntil(() => loaded() && toggle()?.textContent === 'Prior decisions (1)');

    expect(decisions.list).toHaveBeenCalledTimes(1);
    expect(decisions.list).toHaveBeenCalledWith(ID);
    expect(toggle()?.getAttribute('aria-expanded')).toBe('false');
    act(() => toggle()?.click());
    expect(container.querySelector('ol[aria-label="Decision history"]')?.textContent).toContain('Reason: Compliance review');
    expect(decisions.list).toHaveBeenCalledTimes(1);
  });

  it('re-reads only the history, once, after the approve resolves', async () => {
    let resolveApprove: (value: unknown) => void = () => undefined;
    apiMocks.approve.mockReturnValue(new Promise((resolve) => { resolveApprove = resolve; }));
    mount();
    await waitUntil(() => loaded() && toggle()?.textContent === 'Prior decisions (1)');
    const borrowerReads = apiMocks.borrower.mock.calls.length;

    await act(async () => {
      findButton('Approve outreach')?.click();
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    // Pessimistic: nothing re-reads while the write is on the wire.
    expect(decisions.list).toHaveBeenCalledTimes(1);

    await act(async () => {
      resolveApprove({ approved: true, audit_event_id: 'audit-1', approval_id: 'approval-1' });
    });
    await waitUntil(() => decisions.list.mock.calls.length === 2);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(decisions.list).toHaveBeenCalledTimes(2);
    // The audited reads are never repeated for it.
    expect(apiMocks.borrower.mock.calls.length).toBe(borrowerReads);
    expect(queryClient.getQueryState(['mip', 'borrower', ID])).toBeUndefined();
  });

  it('re-reads nothing after a failed approve', async () => {
    apiMocks.approve.mockRejectedValue(new Error('Lakebase unavailable'));
    mount();
    await waitUntil(() => loaded() && toggle()?.textContent === 'Prior decisions (1)');

    await act(async () => {
      findButton('Approve outreach')?.click();
    });
    await waitUntil(() => container.textContent?.includes("Couldn't write approval") === true);
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(decisions.list).toHaveBeenCalledTimes(1);
  });

  describe('the reject reason has no default', () => {
    const reason = () => container.querySelector<HTMLSelectElement>('[data-testid="offer-action-bar"] form select');
    function choose(value: string) {
      act(() => {
        const select = reason()!;
        select.value = value;
        select.dispatchEvent(new Event('change', { bubbles: true }));
      });
    }
    async function openReject() {
      mount();
      await waitUntil(() => loaded());
      act(() => findButton('Reject')?.click());
      await waitUntil(() => findButton('Confirm reject') !== undefined);
    }

    it('opens on "Choose a reason" and a submit without one focuses Reason and sends nothing', async () => {
      await openReject();
      expect(reason()?.value).toBe('');
      expect(reason()?.options[0].textContent).toBe('Choose a reason');
      const confirm = findButton('Confirm reject')!;
      expect(confirm.getAttribute('aria-disabled')).toBe('true');
      expect(confirm.disabled).toBe(false);

      // Reason took focus on open; move it away so the submit has to bring it back.
      act(() => (document.activeElement as HTMLElement | null)?.blur());
      expect(document.activeElement).not.toBe(reason());
      await act(async () => {
        confirm.click();
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(apiMocks.reject).not.toHaveBeenCalled();
      expect(document.activeElement).toBe(reason());
      // The bar's own Reject is refused too (the belt in onReject).
      await act(async () => {
        findButton('Reject')?.click();
        await new Promise((resolve) => setTimeout(resolve, 50));
      });
      expect(apiMocks.reject).not.toHaveBeenCalled();
    });

    it('sends the chosen reason with the unchanged body', async () => {
      await openReject();
      choose('data_quality');
      expect(findButton('Confirm reject')?.hasAttribute('aria-disabled')).toBe(false);
      act(() => findButton('Confirm reject')?.click());
      await waitUntil(() => apiMocks.reject.mock.calls.length === 1);
      const { request_id: _requestId, ...body } = apiMocks.reject.mock.calls[0][1] as Record<string, unknown>;
      expect(body).toEqual({
        offer_code: 'rate_term_refi',
        evidence_ids: ['ev-1', 'ev-2'],
        channel: 'email',
        rationale_code: 'data_quality',
        rationale: null,
        campaign_id: null,
        variant_name: null,
      });
    });

    it('Cancel resets the reason, so a reopened review starts on "Choose a reason" again', async () => {
      await openReject();
      choose('low_intent');
      act(() => findButton('Cancel')?.click());
      await waitUntil(() => findButton('Confirm reject') === undefined);
      act(() => findButton('Reject')?.click());
      await waitUntil(() => findButton('Confirm reject') !== undefined);
      expect(reason()?.value).toBe('');
      expect(findButton('Confirm reject')?.getAttribute('aria-disabled')).toBe('true');
    });
  });
});
