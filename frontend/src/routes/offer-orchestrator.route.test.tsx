/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router-dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type {
  Borrower360,
  BorrowerLifecycle,
  OfferRecommendation,
  SavedDraft,
  SavedDraftInput,
} from '../types';

const apiMocks = vi.hoisted(() => ({
  borrower: vi.fn(),
  recommendOffer: vi.fn(),
  borrowerLifecycle: vi.fn(),
  draftOutreach: vi.fn(),
  salesTeam: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
}));

const appMocks = vi.hoisted(() => ({
  approvals: {} as Record<string, 'approved' | 'rejected'>,
  savedDrafts: {} as Record<string, SavedDraft>,
  setApproval: vi.fn(),
  setLastBorrowerId: vi.fn(),
  saveLead: vi.fn(),
  saveDraft: vi.fn(),
  removeSavedDraft: vi.fn(),
  setDrawer: vi.fn(),
}));

vi.mock('../lib/api', () => {
  class ApiError extends Error {
    status: number;

    constructor(message: string, status = 500) {
      super(message);
      this.status = status;
    }
  }
  return {
    api: apiMocks,
    ApiError,
    isAbortError: () => false,
    isWarmingUpError: (error: unknown) => Boolean(
      error && typeof error === 'object' && 'warming' in error,
    ),
    dependencyLabel: (dependency: string) => (
      dependency === 'warehouse' ? 'Warehouse' : 'Dependency'
    ),
  };
});

vi.mock('../components/AppContext', () => ({
  useApp: () => ({
    approvals: appMocks.approvals,
    setApproval: appMocks.setApproval,
    lastBorrowerId: null,
    setLastBorrowerId: appMocks.setLastBorrowerId,
    saveLead: appMocks.saveLead,
    isLeadSaved: () => false,
    savedDrafts: appMocks.savedDrafts,
    saveDraft: appMocks.saveDraft,
    removeSavedDraft: appMocks.removeSavedDraft,
    setDrawer: appMocks.setDrawer,
    showEvidence: true,
    showConfidence: true,
    canAccessAdmin: false,
  }),
}));

vi.mock('../components/activation/ActivationLoopPanel', () => ({
  ActivationLoopPanel: ({ approvalId }: { approvalId: string | null }) => (
    <div data-testid="activation-loop">activation approval {approvalId ?? 'none'}</div>
  ),
}));

import OfferOrchestrator from './offer-orchestrator';
import { clearBorrowerCache } from './offer-orchestrator.cache';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BORROWER_ID = 'B-0000000000001';

const BORROWER = {
  borrower_id: BORROWER_ID,
  display_name: 'Owner 1',
  city: 'Chicago',
  state: 'IL',
  zip: '60601',
  clip: 'CLIP-0001',
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

const RECOMMENDATION: OfferRecommendation = {
  borrower_id: BORROWER_ID,
  offer_code: 'rate_term_refi',
  offer_type: 'refi',
  product_label: 'Rate and term refinance review',
  confidence: 0.94,
  rationale: 'Reviewed lien economics support a refinance conversation.',
  evidence_ids: ['ev-1', 'ev-2'],
  sources: ['mip.gold.borrower_360'],
  alternatives: [],
  thresholds_applied: { min_spread_bps: 75, min_equity_pct: 15 },
};

const LIFECYCLE: BorrowerLifecycle = {
  borrower_id: BORROWER_ID,
  approval_status: 'pending',
  outreach_status: 'none',
};

const DRAFT = {
  borrower_id: BORROWER_ID,
  offer_code: 'rate_term_refi',
  channel: 'email' as const,
  subject: 'Review your mortgage options',
  body: 'A licensed loan officer can review the available options with you.',
  status: 'draft' as const,
  disclosure_version: 'v1',
  disclosure_state: 'IL',
  marketing_eligible: true,
  generation_mode: 'supervisor' as const,
  generator_label: 'Mortgage Growth Supervisor',
  strategy_summary: 'Use a clear, low-pressure review invitation.',
  evidence_summary: ['Rate spread passes the reviewed threshold.'],
  evidence_assets: ['mip.gold.borrower_360'],
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((res) => {
    resolve = res;
  });
  return { promise, resolve };
}

describe('OfferOrchestrator route behavior', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    clearBorrowerCache();
    appMocks.approvals = {};
    appMocks.savedDrafts = {};
    appMocks.setApproval.mockImplementation((borrowerId: string, status: 'approved' | 'rejected') => {
      appMocks.approvals[borrowerId] = status;
    });
    appMocks.saveDraft.mockReset().mockImplementation(async (draft: SavedDraftInput) => {
      const saved: SavedDraft = {
        ...draft,
        saved_at: '2026-07-13T12:00:00Z',
        updated_at: '2026-07-13T12:00:00Z',
      };
      appMocks.savedDrafts[`${saved.borrower_id}::${saved.channel}`] = saved;
      return saved;
    });
    apiMocks.borrower.mockReset().mockResolvedValue(BORROWER);
    apiMocks.recommendOffer.mockReset().mockResolvedValue(RECOMMENDATION);
    apiMocks.borrowerLifecycle.mockReset().mockResolvedValue(LIFECYCLE);
    apiMocks.draftOutreach.mockReset().mockImplementation(
      async (_borrowerId: string, channel: 'email' | 'sms' | 'direct_mail') => ({
        ...DRAFT,
        channel,
        subject: channel === 'sms' ? null : `${channel} subject`,
        body: `${channel} governed body`,
      }),
    );
    apiMocks.salesTeam.mockReset().mockResolvedValue([]);
    apiMocks.approve.mockReset().mockResolvedValue({
      approved: true,
      audit_event_id: 'audit-1',
      approval_id: 'approval-1',
    });
    apiMocks.reject.mockReset().mockResolvedValue({
      rejected: true,
      audit_event_id: 'audit-reject-1',
      approval_id: 'approval-reject-1',
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    queryClient.clear();
    container.remove();
    clearBorrowerCache();
    vi.useRealTimers();
  });

  function mount() {
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
  }

  async function waitUntil(condition: () => boolean, timeoutMs = 8_000) {
    const started = Date.now();
    while (!condition()) {
      if (Date.now() - started > timeoutMs) throw new Error('waitUntil timeout');
      await act(async () => {
        await new Promise((resolve) => setTimeout(resolve, 10));
      });
    }
  }

  function setInputValue(input: HTMLInputElement | HTMLTextAreaElement, value: string) {
    const prototype = input instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(prototype, 'value')!.set!;
    act(() => {
      setter.call(input, value);
      input.dispatchEvent(new Event('input', { bubbles: true }));
    });
  }

  function button(text: string): HTMLButtonElement {
    const match = [...container.querySelectorAll<HTMLButtonElement>('button')].find(
      (candidate) => candidate.textContent?.trim() === text,
    );
    if (!match) throw new Error(`button not found: ${text}`);
    return match;
  }

  it('keeps pending copy honest and approves the edited governed draft', async () => {
    const pendingDraft = deferred<typeof DRAFT>();
    apiMocks.draftOutreach.mockReturnValue(pendingDraft.promise);
    mount();

    await waitUntil(() => container.textContent?.includes('Rate and term refinance review') === true);
    await waitUntil(() => container.querySelector('[data-testid="draft-loading-note"]') !== null);
    expect(container.querySelector('[data-testid="draft-unavailable-note"]')).toBeNull();
    expect(container.textContent).not.toContain('Offer draft unavailable');

    await act(async () => pendingDraft.resolve(DRAFT));
    await waitUntil(() => container.querySelector<HTMLInputElement>('[data-testid="outreach-subject"]')?.value === DRAFT.subject);
    const subject = container.querySelector<HTMLInputElement>('[data-testid="outreach-subject"]')!;
    const body = container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')!;
    setInputValue(subject, 'Operator reviewed subject');
    setInputValue(body, 'Operator reviewed governed body.');

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="hero-approve"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    expect(apiMocks.approve.mock.calls[0][1]).toMatchObject({
      offer_code: 'rate_term_refi',
      evidence_ids: ['ev-1', 'ev-2'],
      draft_subject: 'Operator reviewed subject',
      draft_body: 'Operator reviewed governed body.',
      channel: 'email',
    });
    await waitUntil(() => container.textContent?.includes('Approved') === true);
    expect(appMocks.setApproval).toHaveBeenCalledWith(BORROWER_ID, 'approved');
    expect(container.textContent).toContain('audit: audit-1');
    expect(container.textContent).toContain('approval: approval-1');
  }, 12_000);

  it('hydrates a saved draft over backend copy and marks it saved', async () => {
    appMocks.savedDrafts[`${BORROWER_ID}::email`] = {
      borrower_id: BORROWER_ID,
      offer_code: 'rate_term_refi',
      channel: 'email',
      subject: 'Saved operator subject',
      body: 'Saved operator body.',
      saved_at: '2026-07-12T12:00:00Z',
      updated_at: '2026-07-12T12:00:00Z',
    };
    mount();

    await waitUntil(() => container.querySelector<HTMLInputElement>('[data-testid="outreach-subject"]')?.value === 'Saved operator subject');
    expect(container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.value)
      .toBe('Saved operator body.');
    expect(button('Draft saved').disabled).toBe(false);
  });

  it('keeps the draft dirty and locked until persistence succeeds', async () => {
    const pendingSave = deferred<SavedDraft>();
    appMocks.saveDraft.mockImplementation((draft: SavedDraftInput) => (
      pendingSave.promise.then((saved) => {
        appMocks.savedDrafts[`${draft.borrower_id}::${draft.channel}`] = saved;
        return saved;
      })
    ));
    mount();
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled === false);
    const body = container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')!;
    setInputValue(body, 'Persist this operator edit.');

    act(() => button('Save draft').click());
    await waitUntil(() => container.textContent?.includes('Saving…') === true);
    expect(button('Saving…').disabled).toBe(true);
    expect(body.disabled).toBe(true);
    expect(appMocks.savedDrafts[`${BORROWER_ID}::email`]).toBeUndefined();

    await act(async () => pendingSave.resolve({
      borrower_id: BORROWER_ID,
      offer_code: 'rate_term_refi',
      channel: 'email',
      subject: 'email subject',
      body: 'Persist this operator edit.',
      saved_at: '2026-07-13T12:00:00Z',
      updated_at: '2026-07-13T12:00:00Z',
    }));
    await waitUntil(() => container.textContent?.includes('Draft saved') === true);
    expect(body.disabled).toBe(false);
  });

  it('surfaces persistence failure and preserves the dirty baseline', async () => {
    appMocks.saveDraft.mockRejectedValue(new Error('Lakebase unavailable'));
    mount();
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled === false);
    setInputValue(
      container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')!,
      'Unsaved operator edit.',
    );

    act(() => button('Save draft').click());
    await waitUntil(() => container.textContent?.includes("Couldn't save draft: Lakebase unavailable") === true);
    expect(button('Save draft').disabled).toBe(false);
    expect(appMocks.savedDrafts[`${BORROWER_ID}::email`]).toBeUndefined();

    act(() => button('SMS').click());
    expect(container.textContent).toContain('replaces the unsaved subject and message');
  });

  it('uses persisted lifecycle approval after reload', async () => {
    apiMocks.borrowerLifecycle.mockResolvedValue({
      ...LIFECYCLE,
      approval_status: 'approved',
      approval_id: 'approval-persisted',
      approved_at: '2026-07-12T12:00:00Z',
    });
    mount();

    await waitUntil(() => container.querySelector('[data-testid="activation-loop"]') !== null);
    const approve = container.querySelector<HTMLButtonElement>('[data-testid="hero-approve"]')!;
    expect(approve.textContent).toContain('Approved');
    expect(approve.disabled).toBe(true);
    expect(container.textContent).toContain('activation approval approval-persisted');
    expect(apiMocks.approve).not.toHaveBeenCalled();
  });

  it.each([
    {
      label: 'false response',
      arrange: () => apiMocks.reject.mockResolvedValue({ rejected: false }),
      message: 'Reject endpoint returned rejected=false.',
    },
    {
      label: 'request error',
      arrange: () => apiMocks.reject.mockRejectedValue(new Error('Lakebase unavailable')),
      message: "Couldn't record rejection: Lakebase unavailable",
    },
  ])('keeps approval unchanged when reject returns a $label', async ({ arrange, message }) => {
    arrange();
    mount();
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled === false);

    act(() => button('Reject').click());
    await waitUntil(() => [...container.querySelectorAll('button')].some(
      (candidate) => candidate.textContent?.trim() === 'Confirm reject',
    ));
    act(() => button('Confirm reject').click());
    await waitUntil(() => container.querySelector('[role="alert"]')?.textContent?.includes(message) === true);
    expect(appMocks.setApproval).not.toHaveBeenCalled();
    expect(container.textContent).not.toContain('Rejected');
  });

  it('automatically retries a warming borrower request', async () => {
    vi.useFakeTimers();
    apiMocks.borrower
      .mockRejectedValueOnce({ warming: true, dependency: 'warehouse', correlationId: 'corr-warm' })
      .mockResolvedValue(BORROWER);
    mount();
    await act(async () => {
      for (let index = 0; index < 10; index += 1) await Promise.resolve();
    });
    expect(container.textContent).toContain('Warehouse warming up');
    expect(container.textContent).toContain('attempt 2 of 6');

    await act(async () => vi.advanceTimersByTimeAsync(5_000));
    expect(apiMocks.borrower).toHaveBeenCalledTimes(2);
    expect(container.textContent).toContain('Review and approve outreach');
  });

  it('retries a terminal borrower load failure from the route error state', async () => {
    apiMocks.borrower
      .mockRejectedValueOnce(new Error('warehouse query failed'))
      .mockResolvedValue(BORROWER);
    mount();
    await waitUntil(() => container.textContent?.includes("Couldn't load borrower or offer") === true);
    expect(button('Retry')).toBeTruthy();

    act(() => button('Retry').click());
    await waitUntil(() => container.textContent?.includes('Review and approve outreach') === true);
    expect(apiMocks.borrower).toHaveBeenCalledTimes(2);
  });

  it('retries a terminal draft failure without losing the route', async () => {
    apiMocks.draftOutreach
      .mockRejectedValueOnce(new Error('draft service failed'))
      .mockResolvedValue(DRAFT);
    mount();
    await waitUntil(() => container.textContent?.includes('Offer draft unavailable: draft service failed') === true);
    expect(container.textContent).toContain('Review and approve outreach');

    act(() => button('Retry draft').click());
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.value === DRAFT.body);
    expect(apiMocks.draftOutreach).toHaveBeenCalledTimes(2);
  });

  it('preserves dirty edits until a channel switch is explicitly confirmed', async () => {
    mount();
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled === false);
    const body = container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')!;
    setInputValue(body, 'Unsaved operator edit.');

    act(() => button('SMS').click());
    expect(body.value).toBe('Unsaved operator edit.');
    expect(apiMocks.draftOutreach).toHaveBeenCalledTimes(1);
    await waitUntil(() => container.textContent?.includes(
      'replaces the unsaved subject and message',
    ) === true);
    expect(container.textContent).toContain('replaces the unsaved subject and message');

    act(() => button('Keep current edits').click());
    expect(body.value).toBe('Unsaved operator edit.');
    act(() => button('SMS').click());
    act(() => button('Switch channel').click());
    await waitUntil(() => apiMocks.draftOutreach.mock.calls.length === 2);
    expect(apiMocks.draftOutreach.mock.calls[1][1]).toBe('sms');
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.value === 'sms governed body');
  });
});
