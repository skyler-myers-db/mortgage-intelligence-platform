/**
 * @vitest-environment happy-dom
 *
 * The Offer Orchestrator's READ-COUNT TABLE (wave 4b, 2026-09-21 audit
 * runtime-06 (a)(f), delivery-08 L slice, states-03 item 2).
 *
 * Every open of the approval surface writes VIEW_BORROWER (B: GET
 * /borrowers/{id}), RECOMMEND_OFFER (R: POST /offers/recommend) and
 * DRAFT_OUTREACH (D: POST /outreach/draft); the lifecycle read (L) rides with
 * them. The effect this port replaced re-read all four on every open,
 * re-open, channel switch, Regenerate, reset and Retry (its module cache only
 * hydrated the screen), so each row below pins the query layer to exactly
 * the base counts: no audit row lost, none added.
 */
import { QueryClient, QueryClientProvider, focusManager, onlineManager } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Borrower360, BorrowerLifecycle, OfferRecommendation, SalesTeamMember, SavedDraft } from '../types';
import type { OutreachDraftResult } from '../lib/apiTypes';
import { ApiError } from '../lib/apiTransport';
import { queryKeys } from '../lib/queryKeys';
import { refetchRecoveredQueries } from '../components/healthRecovery';

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

const appMocks = vi.hoisted(() => ({
  approvals: {} as Record<string, 'approved' | 'rejected'>,
  savedDrafts: {} as Record<string, SavedDraft>,
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
    savedDrafts: appMocks.savedDrafts,
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

const A = 'B-0000000000001';
const B_ID = 'B-0000000000002';
const SNAPSHOT = '2026-07-13T12:00:00Z';

function borrower(id: string, score = 91): Borrower360 {
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
    opportunity_score: score,
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

function recommendation(id: string, refreshedAt = SNAPSHOT): OfferRecommendation {
  return {
    borrower_id: id,
    source_refreshed_at: refreshedAt,
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
  return { borrower_id: id, approval_status: 'pending', outreach_status: 'none' };
}

function draft(id: string, channel: OutreachDraftResult['channel']): OutreachDraftResult {
  return {
    generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
    response_hash: 'a'.repeat(64),
    source_refreshed_at: SNAPSHOT,
    borrower_id: id,
    campaign_id: null,
    variant_name: null,
    offer_code: 'rate_term_refi',
    channel,
    subject: channel === 'sms' ? null : 'Review your mortgage options',
    body: `${channel} governed body`,
    status: 'draft',
    disclosure_version: 'v1',
    disclosure_state: 'IL',
    marketing_eligible: true,
    generation_mode: 'supervisor',
    generator_label: 'Mortgage Growth Supervisor',
    strategy_summary: 'Use a clear, low-pressure review invitation.',
    evidence_summary: ['Rate spread passes the reviewed threshold.'],
    evidence_assets: ['mip.gold.borrower_360'],
  };
}

function warehouse503(reason: 'warming_up' | 'retries_exhausted'): ApiError {
  return new ApiError(`warehouse ${reason}`, {
    path: `/api/v1/borrowers/${A}`,
    status: 503,
    retryable: true,
    dependency: 'warehouse',
    reason,
    correlationId: 'corr-reads',
  });
}

interface Counts { B: number; R: number; L: number; D: number }

function counts(id?: string): Counts {
  const forId = (mock: ReturnType<typeof vi.fn>) =>
    mock.mock.calls.filter((call) => id === undefined || call[0] === id).length;
  return {
    B: forId(apiMocks.borrower),
    R: forId(apiMocks.recommendOffer),
    L: forId(apiMocks.borrowerLifecycle),
    D: forId(apiMocks.draftOutreach),
  };
}

function delta(before: Counts, after: Counts): Counts {
  return { B: after.B - before.B, R: after.R - before.R, L: after.L - before.L, D: after.D - before.D };
}

const ONE_EACH: Counts = { B: 1, R: 1, L: 1, D: 1 };
const NONE: Counts = { B: 0, R: 0, L: 0, D: 0 };

describe('Offer Orchestrator read-count table (audit parity with the effect it replaced)', () => {
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
    appMocks.approvals = {};
    appMocks.savedDrafts = {};
    apiMocks.borrower.mockImplementation(async (id: string) => borrower(id));
    apiMocks.recommendOffer.mockImplementation(async (id: string) => recommendation(id));
    apiMocks.borrowerLifecycle.mockImplementation(async (id: string) => lifecycle(id));
    apiMocks.draftOutreach.mockImplementation(async (id: string, channel: OutreachDraftResult['channel']) => draft(id, channel));
    apiMocks.salesTeam.mockResolvedValue([]);
    apiMocks.approve.mockResolvedValue({ approved: true, audit_event_id: 'audit-here', approval_id: 'approval-here' });
    apiMocks.auditReceipt.mockReturnValue(new Promise(() => undefined));
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    navigate = null;
    focusManager.setFocused(undefined);
    onlineManager.setOnline(true);
    vi.useRealTimers();
  });

  function mount(entry = `/offer-orchestrator/${A}`, client = queryClient) {
    act(() => {
      root.render(
        <QueryClientProvider client={client}>
          <MemoryRouter initialEntries={[entry]}>
            <NavigateProbe />
            <Routes>
              <Route path="/offer-orchestrator/:id" element={<OfferOrchestrator />} />
              <Route path="/lead-queue" element={<div data-testid="elsewhere" />} />
            </Routes>
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

  /** Let every pending timer and notification run (the draft's gcTime 0 included). */
  async function settle(ms = 30) {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, ms));
    });
  }

  const reviewBody = () => container.querySelector<HTMLElement>('[data-testid="outreach-draft"]');
  /** The audited draft is loaded and current: Approve is armed. */
  const loaded = (channel: OutreachDraftResult['channel'] = 'email') => {
    const body = reviewBody();
    return body !== null && body.getAttribute('aria-disabled') !== 'true' && body.textContent === `${channel} governed body`;
  };
  function button(text: string): HTMLButtonElement {
    const match = [...container.querySelectorAll<HTMLButtonElement>('button')].find(
      (candidate) => candidate.textContent?.trim() === text,
    );
    if (!match) throw new Error(`button not found: ${text}`);
    return match;
  }
  async function go(path: string) {
    await act(async () => {
      void navigate?.(path);
    });
  }
  /** Leave the offer, let the page let go of its reads, and come back. */
  async function reopen(id = A) {
    await go('/lead-queue');
    await waitUntil(() => container.querySelector('[data-testid="elsewhere"]') !== null);
    await settle();
    await go(`/offer-orchestrator/${id}`);
  }

  it('first open: B1 R1 L1 D1', async () => {
    mount();
    await waitUntil(() => loaded());
    await settle();
    expect(counts()).toEqual(ONE_EACH);
  });

  it('re-open within 5 minutes: +1 each (a hydrated snapshot never skips the audited re-read)', async () => {
    mount();
    await waitUntil(() => loaded());
    const before = counts();
    await reopen();
    await waitUntil(() => loaded() && counts().D === before.D + 1);
    await settle();
    expect(delta(before, counts())).toEqual(ONE_EACH);
  });

  it('re-open after an approve: +1 each, and the approve itself reads nothing', async () => {
    mount();
    await waitUntil(() => loaded());
    const before = counts();
    await act(async () => {
      button('Approve outreach').click();
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    await settle(50);
    expect(delta(before, counts())).toEqual(NONE);
    await reopen();
    await waitUntil(() => counts().D === before.D + 1 && counts().B === before.B + 1);
    await settle();
    expect(delta(before, counts())).toEqual(ONE_EACH);
  });

  it('a lazily loaded child mounting after the snapshot settles adds B+0 R+0 L+0 (single observer)', async () => {
    mount();
    await waitUntil(() => loaded());
    await settle();
    const before = counts();
    await act(async () => {
      button('Approve outreach').click();
    });
    // The Decision receipt is its own lazy chunk, mounted only now: it gets
    // the decision as props and never observes the snapshot query.
    await waitUntil(() => container.querySelector('[data-testid="decision-receipt-pending"]') !== null);
    await settle(100);
    expect(delta(before, counts())).toEqual(NONE);
  });

  it('reload (a fresh QueryClient): B1 R1 L1 D1 again', async () => {
    mount();
    await waitUntil(() => loaded());
    const before = counts();
    act(() => root.unmount());
    root = createRoot(container);
    const fresh = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    mount(`/offer-orchestrator/${A}`, fresh);
    await waitUntil(() => loaded());
    await settle();
    expect(delta(before, counts())).toEqual(ONE_EACH);
    fresh.clear();
  });

  it('dossier then offer: the seeded Borrower 360 entry is never read, rendered or overwritten (B = 1)', async () => {
    const seeded = borrower(A, 13);
    queryClient.setQueryData(queryKeys.borrower(A), seeded);
    let releaseBorrower: (value: Borrower360) => void = () => undefined;
    apiMocks.borrower.mockImplementationOnce(() => new Promise<Borrower360>((resolve) => {
      releaseBorrower = resolve;
    }));
    mount();
    await settle();
    // The dossier's cached score (13) is not what the approver is shown.
    expect([...container.querySelectorAll('.score')].map((el) => el.textContent)).not.toContain('13');
    await act(async () => releaseBorrower(borrower(A)));
    await waitUntil(() => loaded());
    await settle();
    expect(counts().B).toBe(1);
    expect([...container.querySelectorAll('.score')].map((el) => el.textContent)).toContain('91');
    expect([...container.querySelectorAll('.score')].map((el) => el.textContent)).not.toContain('13');
    // The approval surface keeps its own entry: the dossier's is untouched.
    expect(queryClient.getQueryData(queryKeys.borrower(A))).toBe(seeded);
  });

  it('channel switch: +1 each', async () => {
    mount();
    await waitUntil(() => loaded());
    const before = counts();
    act(() => button('SMS').click());
    await waitUntil(() => loaded('sms'));
    await settle();
    expect(delta(before, counts())).toEqual(ONE_EACH);
    expect(apiMocks.draftOutreach.mock.calls[apiMocks.draftOutreach.mock.calls.length - 1][1]).toBe('sms');
  });

  it('Regenerate: +1 each', async () => {
    mount();
    await waitUntil(() => loaded());
    const before = counts();
    act(() => button('Regenerate').click());
    act(() => button('Replace current draft').click());
    await waitUntil(() => counts().D === before.D + 1);
    await waitUntil(() => loaded());
    await settle();
    expect(delta(before, counts())).toEqual(ONE_EACH);
  });

  it('reset of a saved draft: +1 each', async () => {
    appMocks.savedDrafts[`${A}::email`] = {
      borrower_id: A,
      generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
      response_hash: 'a'.repeat(64),
      offer_code: 'rate_term_refi',
      channel: 'email',
      subject: 'Review your mortgage options',
      body: 'email governed body',
      saved_at: '2026-07-13T12:00:00Z',
      updated_at: '2026-07-13T12:00:00Z',
    };
    mount();
    await waitUntil(() => loaded());
    const before = counts();
    act(() => button('Reset draft').click());
    await waitUntil(() => counts().D === before.D + 1);
    await waitUntil(() => loaded());
    await settle();
    expect(delta(before, counts())).toEqual(ONE_EACH);
  });

  it('terminal-error Retry: +1 each', async () => {
    apiMocks.borrower.mockRejectedValueOnce(new Error('warehouse query failed'));
    mount();
    await waitUntil(() => container.textContent?.includes("Couldn't load borrower or offer") === true);
    await settle();
    const before = counts();
    expect(before).toEqual(ONE_EACH);
    act(() => button('Retry').click());
    await waitUntil(() => loaded() && counts().D === before.D + 1);
    await settle();
    expect(delta(before, counts())).toEqual(ONE_EACH);
  });

  it('pager A -> B -> A: A reads twice, B once, and no other borrower is read', async () => {
    mount();
    await waitUntil(() => loaded());
    await go(`/offer-orchestrator/${B_ID}`);
    await waitUntil(() => counts(B_ID).D === 1 && loaded());
    await settle();
    await go(`/offer-orchestrator/${A}`);
    await waitUntil(() => counts(A).D === 2 && loaded());
    await settle();
    expect(counts(A)).toEqual({ B: 2, R: 2, L: 2, D: 2 });
    expect(counts(B_ID)).toEqual(ONE_EACH);
    expect(counts()).toEqual({ B: 3, R: 3, L: 3, D: 3 });
  });

  it('window focus and online events: +0', async () => {
    mount();
    await waitUntil(() => loaded());
    await settle();
    const before = counts();
    act(() => {
      focusManager.setFocused(false);
      focusManager.setFocused(true);
      onlineManager.setOnline(false);
      onlineManager.setOnline(true);
    });
    await settle(100);
    expect(delta(before, counts())).toEqual(NONE);
  });

  it('mismatch x1 then match: B2 R2 L2 D1, the second borrower read with fresh=true', async () => {
    apiMocks.recommendOffer.mockResolvedValueOnce(recommendation(A, '2026-07-13T12:05:00Z'));
    mount();
    await waitUntil(() => loaded() && counts().B === 2);
    await settle();
    expect(counts()).toEqual({ B: 2, R: 2, L: 2, D: 1 });
    expect(apiMocks.borrower.mock.calls[0][2]).toBe(false);
    expect(apiMocks.borrower.mock.calls[1][2]).toBe(true);
  });

  it('warming_up once then success: B2 R2 L2 (D1)', async () => {
    vi.useFakeTimers();
    apiMocks.borrower.mockRejectedValueOnce(warehouse503('warming_up'));
    mount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(container.textContent).toContain('Warehouse warming up');
    await act(async () => {
      await vi.advanceTimersByTimeAsync(5_000);
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(1);
    });
    expect(counts()).toEqual({ B: 2, R: 2, L: 2, D: 1 });
    expect(container.textContent).toContain('Review and approve outreach');
  });

  it('retries_exhausted 503 then a health recovery edge: exactly +1 B/R/L and no /draft', async () => {
    apiMocks.borrower.mockRejectedValueOnce(warehouse503('retries_exhausted'));
    mount();
    await waitUntil(() => container.textContent?.includes("Couldn't load borrower or offer") === true);
    await settle();
    const before = counts();
    expect(before).toEqual(ONE_EACH);
    // Non-vacuity: an unrelated dependency recovering re-reads nothing.
    act(() => refetchRecoveredQueries(queryClient, ['genie']));
    await settle(50);
    expect(delta(before, counts())).toEqual(NONE);
    act(() => refetchRecoveredQueries(queryClient, ['warehouse']));
    await waitUntil(() => loaded());
    await settle(50);
    expect(delta(before, counts())).toEqual({ B: 1, R: 1, L: 1, D: 0 });
  });

  it('the assignment roster: its own key, active loan officers and sales managers, one read per minute', async () => {
    const roster: SalesTeamMember[] = [
      { email: 'lo.a@summit-mortgage.example', display_label: 'Loan Officer A', role: 'loan_officer', capacity_per_day: 20, active: true },
      { email: 'sm.b@summit-mortgage.example', display_label: 'Sales Manager B', role: 'sales_manager', capacity_per_day: 5, active: true },
      { email: 'lo.c@summit-mortgage.example', display_label: 'Loan Officer C', role: 'loan_officer', capacity_per_day: 20, active: false },
      { email: 'admin.d@summit-mortgage.example', display_label: 'Admin D', role: 'admin', capacity_per_day: 0, active: true },
    ];
    apiMocks.salesTeam.mockResolvedValue(roster);
    // Lead Queue / Sales ops cache a loan-officer-only list under salesTeam():
    // Offer must not read (or overwrite) it.
    const loOnly = roster.filter((member) => member.role === 'loan_officer');
    queryClient.setQueryData(queryKeys.salesTeam(), loOnly);
    mount();
    await waitUntil(() => loaded() && container.querySelectorAll('#lo-assign option').length > 1);
    const options = () => [...container.querySelectorAll<HTMLOptionElement>('#lo-assign option')].map((option) => option.value);
    expect(options()).toEqual(['', 'lo.a@summit-mortgage.example', 'sm.b@summit-mortgage.example']);
    expect(apiMocks.salesTeam).toHaveBeenCalledTimes(1);
    expect(queryClient.getQueryData(queryKeys.salesTeam())).toBe(loOnly);
    // A re-open within the minute reuses the roster (no audit row either way).
    await reopen();
    await waitUntil(() => loaded() && counts().D === 2);
    expect(apiMocks.salesTeam).toHaveBeenCalledTimes(1);
  });

  it('a roster failure leaves assignment empty and approval available', async () => {
    apiMocks.salesTeam.mockRejectedValue(new Error('sales team unavailable'));
    mount();
    await waitUntil(() => loaded());
    await settle(50);
    expect([...container.querySelectorAll<HTMLOptionElement>('#lo-assign option')].map((option) => option.value)).toEqual(['']);
    expect(button('Approve outreach').disabled).toBe(false);
  });

  it('a draft that also holds a retryable 503 re-POSTs once on the same recovery edge', async () => {
    apiMocks.borrower.mockRejectedValueOnce(warehouse503('retries_exhausted'));
    apiMocks.draftOutreach.mockRejectedValueOnce(warehouse503('retries_exhausted'));
    mount();
    await waitUntil(() => container.textContent?.includes("Couldn't load borrower or offer") === true);
    await settle();
    const before = counts();
    act(() => refetchRecoveredQueries(queryClient, ['warehouse']));
    await waitUntil(() => loaded());
    await settle(50);
    expect(delta(before, counts())).toEqual(ONE_EACH);
  });
});
