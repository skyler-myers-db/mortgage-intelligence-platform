/**
 * @vitest-environment happy-dom
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes } from 'react-router';
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
  source_refreshed_at: '2026-07-13T12:00:00Z',
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
  source_refreshed_at: '2026-07-13T12:00:00Z',
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
  generation_id: 'aaaaaaaa-aaaa-4aaa-8aaa-aaaaaaaaaaaa',
  response_hash: 'a'.repeat(64),
  source_refreshed_at: '2026-07-13T12:00:00Z',
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
        offer_code: DRAFT.offer_code,
        channel: DRAFT.channel,
        subject: DRAFT.subject,
        body: DRAFT.body,
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
      async (
        _borrowerId: string,
        channel: 'email' | 'sms' | 'direct_mail',
        _signal?: AbortSignal,
        campaign?: { campaign_id: string; variant_name: string },
      ) => ({
        ...DRAFT,
        channel,
        subject: channel === 'sms' ? null : `${channel} subject`,
        body: `${channel} governed body`,
        campaign_id: campaign?.campaign_id ?? null,
        variant_name: campaign?.variant_name ?? null,
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

  function mount(initialEntry = `/offer-orchestrator/${BORROWER_ID}`) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={[initialEntry]}>
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

  function button(text: string): HTMLButtonElement {
    const match = [...container.querySelectorAll<HTMLButtonElement>('button')].find(
      (candidate) => candidate.textContent?.trim() === text,
    );
    if (!match) throw new Error(`button not found: ${text}`);
    return match;
  }

  it('keeps pending copy immutable and approves the exact governed draft', async () => {
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
    expect(subject.readOnly).toBe(true);
    expect(body.readOnly).toBe(true);

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="hero-approve"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    expect(apiMocks.approve.mock.calls[0][1]).toMatchObject({
      offer_code: 'rate_term_refi',
      evidence_ids: ['ev-1', 'ev-2'],
      draft_subject: DRAFT.subject,
      draft_body: DRAFT.body,
      draft_generation_id: DRAFT.generation_id,
      draft_response_hash: DRAFT.response_hash,
      draft_source_refreshed_at: DRAFT.source_refreshed_at,
      channel: 'email',
    });
    await waitUntil(() => container.textContent?.includes('Approved') === true);
    expect(appMocks.setApproval).toHaveBeenCalledWith(BORROWER_ID, 'approved');
    expect(container.textContent).toContain('audit: audit-1');
    expect(container.textContent).toContain('approval: approval-1');
  }, 12_000);

  it('keeps saved campaign provenance attached through draft and approval', async () => {
    const campaignId = '7e373ef5-d4b6-4fea-b555-0cb925987a72';
    const variantName = 'Supervisor B';
    mount(
      `/offer-orchestrator/${BORROWER_ID}?campaign_id=${campaignId}`
      + `&variant_name=${encodeURIComponent(variantName)}`,
    );

    await waitUntil(() => (
      container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled
      === false
    ));
    const provenance = container.querySelector('[data-testid="offer-campaign-provenance"]');
    expect(provenance?.textContent).toContain('Campaign-bound draft');
    expect(provenance?.textContent).toContain('campaign 7e373ef5-d4b');
    expect(provenance?.textContent).toContain(`variant ${variantName}`);
    expect(provenance?.textContent).toContain('Mortgage Growth Supervisor');
    expect(provenance?.textContent).toContain('draft proof aaaaaaaaaaaa');
    expect(apiMocks.draftOutreach).toHaveBeenCalledWith(
      BORROWER_ID,
      'email',
      expect.any(AbortSignal),
      { campaign_id: campaignId, variant_name: variantName },
    );
    const channelRestriction = container.querySelector('[data-testid="campaign-channel-restriction"]');
    const email = button('EMAIL');
    const sms = button('SMS');
    const directMail = button('Direct mail');
    expect(channelRestriction?.textContent).toContain('saved audited EMAIL copy');
    expect(email.disabled).toBe(false);
    expect(sms.disabled).toBe(true);
    expect(directMail.disabled).toBe(true);
    expect(sms.getAttribute('aria-describedby')).toBe(channelRestriction?.id);
    expect(directMail.getAttribute('aria-describedby')).toBe(channelRestriction?.id);

    act(() => sms.click());
    act(() => directMail.click());
    expect(apiMocks.draftOutreach).toHaveBeenCalledTimes(1);

    await act(async () => {
      container.querySelector<HTMLButtonElement>('[data-testid="hero-approve"]')!.click();
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    expect(apiMocks.approve.mock.calls[0][1]).toMatchObject({
      campaign_id: campaignId,
      variant_name: variantName,
      channel: 'email',
    });
  });

  it('keeps saved campaign provenance attached through rejection', async () => {
    const campaignId = '7e373ef5-d4b6-4fea-b555-0cb925987a72';
    const variantName = 'Supervisor A';
    mount(
      `/offer-orchestrator/${BORROWER_ID}?campaign_id=${campaignId}`
      + `&variant_name=${encodeURIComponent(variantName)}`,
    );
    await waitUntil(() => (
      container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled
      === false
    ));

    act(() => button('Reject').click());
    await waitUntil(() => [...container.querySelectorAll('button')].some(
      (candidate) => candidate.textContent?.trim() === 'Confirm reject',
    ));
    act(() => button('Confirm reject').click());

    await waitUntil(() => apiMocks.reject.mock.calls.length === 1);
    expect(apiMocks.reject.mock.calls[0][1]).toMatchObject({
      campaign_id: campaignId,
      variant_name: variantName,
    });
  });

  it('never hydrates operator-saved copy over a fresh audited proof', async () => {
    appMocks.savedDrafts[`${BORROWER_ID}::email`] = {
      borrower_id: BORROWER_ID,
      generation_id: '11111111-1111-4111-8111-111111111111',
      response_hash: 'a'.repeat(64),
      offer_code: 'rate_term_refi',
      channel: 'email',
      subject: 'Saved operator subject',
      body: 'Saved operator body.',
      saved_at: '2026-07-12T12:00:00Z',
      updated_at: '2026-07-12T12:00:00Z',
    };
    mount();

    await waitUntil(() => container.querySelector<HTMLInputElement>('[data-testid="outreach-subject"]')?.value === 'email subject');
    expect(container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.value)
      .toBe('email governed body');
    expect(button('Save draft').disabled).toBe(false);
  });

  it('shows the durable audit reference after a rejection succeeds', async () => {
    mount();
    await waitUntil(() => (
      container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled
      === false
    ));

    act(() => button('Reject').click());
    await waitUntil(() => [...container.querySelectorAll('button')].some(
      (candidate) => candidate.textContent?.trim() === 'Confirm reject',
    ));
    act(() => button('Confirm reject').click());

    await waitUntil(() => container.textContent?.includes('Rejected') === true);
    expect(appMocks.setApproval).toHaveBeenCalledWith(BORROWER_ID, 'rejected');
    expect(container.textContent).toContain('audit: audit-reject-1');
  });

  it('keeps exact audited copy locked while persistence succeeds', async () => {
    const pendingSave = deferred<SavedDraft>();
    appMocks.saveDraft.mockImplementation((draft: SavedDraftInput) => (
      pendingSave.promise.then((saved) => {
        appMocks.savedDrafts[`${draft.borrower_id}::${saved.channel}`] = saved;
        return saved;
      })
    ));
    mount();
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled === false);
    const body = container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')!;
    expect(body.readOnly).toBe(true);

    act(() => button('Save draft').click());
    await waitUntil(() => container.textContent?.includes('Saving…') === true);
    expect(button('Saving…').disabled).toBe(true);
    expect(body.disabled).toBe(true);
    expect(appMocks.savedDrafts[`${BORROWER_ID}::email`]).toBeUndefined();

    await act(async () => pendingSave.resolve({
      borrower_id: BORROWER_ID,
      generation_id: DRAFT.generation_id,
      response_hash: DRAFT.response_hash,
      offer_code: 'rate_term_refi',
      channel: 'email',
      subject: 'email subject',
      body: 'email governed body',
      saved_at: '2026-07-13T12:00:00Z',
      updated_at: '2026-07-13T12:00:00Z',
    }));
    await waitUntil(() => container.textContent?.includes('Draft saved') === true);
    expect(body.disabled).toBe(false);
  });

  it('surfaces persistence failure without unlocking audited copy', async () => {
    appMocks.saveDraft.mockRejectedValue(new Error('Lakebase unavailable'));
    mount();
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled === false);
    expect(container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.readOnly)
      .toBe(true);

    act(() => button('Save draft').click());
    await waitUntil(() => container.textContent?.includes("Couldn't save draft: Lakebase unavailable") === true);
    expect(button('Save draft').disabled).toBe(false);
    expect(appMocks.savedDrafts[`${BORROWER_ID}::email`]).toBeUndefined();

    act(() => button('SMS').click());
    await waitUntil(() => apiMocks.draftOutreach.mock.calls.length === 2);
    expect(apiMocks.draftOutreach.mock.calls[1][1]).toBe('sms');
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
      local: 'rejected' as const,
      durable: 'approved' as const,
      expected: 'Approved',
      absent: 'Rejected',
    },
    {
      local: 'approved' as const,
      durable: 'rejected' as const,
      expected: 'Rejected',
      absent: 'Approved · governed internal queue',
    },
  ])(
    'renders durable $durable state over conflicting local $local state',
    async ({ local, durable, expected, absent }) => {
      appMocks.approvals[BORROWER_ID] = local;
      apiMocks.borrowerLifecycle.mockResolvedValue({
        ...LIFECYCLE,
        approval_status: durable,
        approval_id: `approval-${durable}`,
      });
      mount();

      await waitUntil(() => container.textContent?.includes(expected) === true);
      expect(container.textContent).not.toContain(absent);
    },
  );

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

  it('refuses to combine borrower and offer data from different refresh snapshots', async () => {
    apiMocks.recommendOffer.mockResolvedValue({
      ...RECOMMENDATION,
      source_refreshed_at: '2026-07-13T12:05:00Z',
    });
    mount();

    await waitUntil(() => apiMocks.recommendOffer.mock.calls.length === 6, 10_000);
    await waitUntil(() => container.textContent?.includes(
      'The borrower and offer snapshots changed while loading',
    ) === true);
    expect(apiMocks.borrower.mock.calls.slice(1).every((call) => call[2] === true)).toBe(true);
    expect(container.textContent).not.toContain('Review and approve outreach');
  }, 12_000);

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

  it('switches channels by loading a new exact audited draft', async () => {
    mount();
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.disabled === false);
    const body = container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')!;
    expect(body.readOnly).toBe(true);

    act(() => button('SMS').click());
    await waitUntil(() => apiMocks.draftOutreach.mock.calls.length === 2);
    expect(apiMocks.draftOutreach.mock.calls[1][1]).toBe('sms');
    await waitUntil(() => container.querySelector<HTMLTextAreaElement>('[data-testid="outreach-draft"]')?.value === 'sms governed body');
  });
});
