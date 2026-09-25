/**
 * @vitest-environment happy-dom
 *
 * Carryover #12 at the route: the routing line comes from the approve
 * response and is kept for the borrower it was made for. Paging to another
 * borrower whose durable approval reads back a receipt must not carry the
 * first borrower's assignee and follow-up onto it (offer-orchestrator.tsx
 * passes routing only when approvalRouting.id matches the route id).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Borrower360, BorrowerLifecycle, OfferRecommendation } from '../types';
import type { DecisionReceipt as DecisionReceiptPayload } from '../lib/apiTypes';

const apiMocks = vi.hoisted(() => ({
  borrower: vi.fn(),
  recommendOffer: vi.fn(),
  borrowerLifecycle: vi.fn(),
  draftOutreach: vi.fn(),
  salesTeam: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  auditReceipt: vi.fn(),
  borrowerProof: vi.fn(),
}));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status: number | null = null;
  }
  return {
    api: apiMocks,
    ApiError,
    isAbortError: () => false,
    isWarmingUpError: () => false,
    dependencyLabel: () => 'Dependency',
  };
});

// The session's approvals map: an approve records into it, as AppContext does.
const appState = vi.hoisted(() => ({ approvals: {} as Record<string, 'approved' | 'rejected'> }));

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    approvals: appState.approvals,
    setApproval: (borrowerId: string, status: 'approved' | 'rejected') => {
      appState.approvals[borrowerId] = status;
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
import { clearBorrowerCache } from './offer-orchestrator.cache';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;
vi.setConfig({ testTimeout: 30_000 });

const DECIDED_HERE = 'B-0000000000001';
const DECIDED_EARLIER = 'B-0000000000002';
const SNAPSHOT = '2026-07-13T12:00:00Z';

function borrower(id: string): Borrower360 {
  return {
    borrower_id: id,
    source_refreshed_at: SNAPSHOT,
    display_name: 'Owner 1',
    city: 'Chicago',
    state: 'IL',
    zip: '60601',
    clip: `CLIP-${id}`,
    segment_codes: ['itm'],
    equity_estimate: 180_000,
    rate_spread_bps: 125,
    opportunity_score: 91,
    confidence: 0.94,
    recommended_offer_code: 'rate_term_refi',
    recommended_offer: 'Rate and term refinance review',
    why_now: 'Current lien rate exceeds the reviewed market threshold.',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
    clip_id: 'clip-1',
    owner_link_id: 'owner-1',
    subject_property: '100 Main St',
    avm_value: 500_000,
    current_lien_balance: 320_000,
    current_rate: 7.25,
    ltv: 0.64,
    related_property_count: 1,
    trigger_timeline: [],
    evidence_events: [],
    why_panel: {
      rate_spread_bps: 125,
      market_rate: 6,
      equity_pct: 36,
      in_the_money: true,
      in_the_money_reason: 'Reviewed rate and equity thresholds pass.',
      min_spread_bps: 75,
      min_equity_pct: 15,
      sources: ['mip.gold.borrower_360'],
    },
  } as Borrower360;
}

function recommendation(id: string): OfferRecommendation {
  return {
    borrower_id: id,
    source_refreshed_at: SNAPSHOT,
    offer_code: 'rate_term_refi',
    offer_type: 'refi',
    product_label: 'Rate and term refinance review',
    confidence: 0.94,
    rationale: 'Reviewed lien economics support a refinance conversation.',
    evidence_ids: ['ev-1'],
    sources: ['mip.gold.borrower_360'],
    alternatives: [],
    thresholds_applied: { min_spread_bps: 75, min_equity_pct: 15 },
  };
}

function lifecycle(id: string): BorrowerLifecycle {
  return id === DECIDED_EARLIER
    ? { borrower_id: id, approval_status: 'approved', outreach_status: 'none', approval_id: 'approval-earlier', audit_event_id: 'audit-earlier', approved_at: '2026-07-12T12:00:00Z' }
    : { borrower_id: id, approval_status: 'pending', outreach_status: 'none' };
}

function ledgerReceipt(auditEventId: string): DecisionReceiptPayload {
  const earlier = auditEventId === 'audit-earlier';
  return {
    audit_event_id: auditEventId,
    event_type: 'APPROVE',
    decision: 'approved',
    approval_id: earlier ? 'approval-earlier' : 'approval-here',
    borrower_id: earlier ? DECIDED_EARLIER : DECIDED_HERE,
    offer_code: 'rate_term_refi',
    offer_label: 'Rate and term refinance review',
    campaign_id: null,
    variant_name: null,
    channel: 'email',
    rationale_code: null,
    copy_generation_id: null,
    copy_hash: null,
    approver: 'ledger.approver@summit.example',
    request_id: 'cccccccc-cccc-4ccc-8ccc-cccccccccccc',
    correlation_id: 'corr-ledger-0001',
    created_at: '2026-07-13T12:05:00Z',
    evidence_ids: ['ev-1'],
    evidence_assets: [],
  };
}

describe('OfferOrchestrator approval routing line', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;
  let navigate: NavigateFunction | null = null;

  function NavigateProbe() {
    navigate = useNavigate();
    return null;
  }

  beforeEach(() => {
    vi.clearAllMocks();
    clearBorrowerCache();
    appState.approvals = {};
    apiMocks.borrower.mockImplementation(async (id: string) => borrower(id));
    apiMocks.recommendOffer.mockImplementation(async (id: string) => recommendation(id));
    apiMocks.borrowerLifecycle.mockImplementation(async (id: string) => lifecycle(id));
    apiMocks.draftOutreach.mockImplementation(async (id: string, channel: 'email' | 'sms' | 'direct_mail') => ({
      generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      response_hash: 'a'.repeat(64),
      source_refreshed_at: SNAPSHOT,
      borrower_id: id,
      offer_code: 'rate_term_refi',
      channel,
      subject: 'Review your mortgage options',
      body: 'A licensed loan officer can review the available options with you.',
      status: 'draft',
      disclosure_version: 'v1',
      disclosure_state: 'IL',
      marketing_eligible: true,
      generation_mode: 'supervisor',
      generator_label: 'Mortgage Growth Supervisor',
      strategy_summary: 'Use a clear, low-pressure review invitation.',
      evidence_summary: ['Rate spread passes the reviewed threshold.'],
      evidence_assets: ['mip.gold.borrower_360'],
      campaign_id: null,
      variant_name: null,
    }));
    apiMocks.salesTeam.mockResolvedValue([]);
    apiMocks.approve.mockResolvedValue({
      approved: true,
      audit_event_id: 'audit-here',
      approval_id: 'approval-here',
      assigned_to_email: 'lo.a@summit-mortgage.example',
      follow_up_at: '2026-07-19T15:00:00Z',
    });
    apiMocks.auditReceipt.mockImplementation(async (id: string) => ledgerReceipt(id));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    clearBorrowerCache();
    navigate = null;
  });

  async function waitUntil(condition: () => boolean, timeoutMs = 8_000) {
    const started = Date.now();
    while (!condition()) {
      if (Date.now() - started > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  const receiptFor = (auditId: string) =>
    container.querySelector<HTMLElement>(`[data-testid="decision-receipt"][data-audit-event-id="${auditId}"]`);
  const routing = () => container.querySelector('[data-testid="decision-receipt-routing"]');

  it('keeps the approve response routing on the borrower approved here, never on the next one paged to', async () => {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[`/offer-orchestrator/${DECIDED_HERE}`]}>
            <NavigateProbe />
            <Routes>
              <Route path="/offer-orchestrator/:id" element={<OfferOrchestrator />} />
            </Routes>
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
    // The audited draft is loaded and current: Approve is armed.
    await waitUntil(() => {
      const body = container.querySelector('[data-testid="outreach-draft"]');
      return body !== null && body.getAttribute('aria-disabled') !== 'true';
    });

    await act(async () => {
      [...container.querySelectorAll<HTMLButtonElement>('button')]
        .find((candidate) => candidate.textContent?.trim() === 'Approve outreach')
        ?.click();
    });
    await waitUntil(() => receiptFor('audit-here') !== null);
    // Positive control: the approval made here shows where it was routed.
    expect(routing()?.textContent).toContain('lo.a@summit-mortgage.example');

    await act(async () => {
      void navigate?.(`/offer-orchestrator/${DECIDED_EARLIER}`);
    });
    await waitUntil(() => receiptFor('audit-earlier') !== null);
    expect(apiMocks.auditReceipt).toHaveBeenCalledWith('audit-earlier', expect.anything());
    // The earlier approval has no routing from this view: nothing carries over.
    expect(routing()).toBeNull();
    expect(container.textContent).not.toContain('lo.a@summit-mortgage.example');
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
  });
});
