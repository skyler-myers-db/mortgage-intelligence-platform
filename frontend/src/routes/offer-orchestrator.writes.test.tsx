/**
 * @vitest-environment happy-dom
 *
 * The Offer Orchestrator's governed writes on the mutation layer (wave 4b,
 * stack-09 item 1, states-07 item 3, runtime-06 (b)), through the real route.
 *
 * The approve / reject bodies must be EXACTLY what the route sent before the
 * port (only request_id is new in the second argument: it used to be minted
 * inside api.approve per click, now once per intent). The goldens below are
 * written out field by field from the pre-port handler.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Borrower360, BorrowerLifecycle, OfferRecommendation, SalesTeamMember } from '../types';
import type { OutreachDraftResult } from '../lib/apiTypes';

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
const CAMPAIGN_ID = '7e373ef5-d4b6-4fea-b555-0cb925987a72';
const VARIANT = 'Supervisor B';
const LO: SalesTeamMember = {
  email: 'lo.a@summit-mortgage.example',
  display_label: 'Loan Officer A',
  role: 'loan_officer',
  region: 'IL',
  capacity_per_day: 20,
  active: true,
};

const BORROWER = {
  borrower_id: ID,
  source_refreshed_at: SNAPSHOT,
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
  borrower_id: ID,
  source_refreshed_at: SNAPSHOT,
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

const LIFECYCLE: BorrowerLifecycle = { borrower_id: ID, approval_status: 'pending', outreach_status: 'none' };

function draftFor(channel: OutreachDraftResult['channel'], campaign?: { campaign_id: string; variant_name: string }): OutreachDraftResult {
  return {
    generation_id: `gen-${channel}`,
    response_hash: 'a'.repeat(64),
    source_refreshed_at: SNAPSHOT,
    borrower_id: ID,
    campaign_id: campaign?.campaign_id ?? null,
    variant_name: campaign?.variant_name ?? null,
    offer_code: 'rate_term_refi',
    channel,
    subject: channel === 'sms' ? null : `${channel} subject`,
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

/** The pre-port approve body for the email draft (offer-orchestrator.tsx before wave 4b). */
const GOLDEN_EMAIL_APPROVE = {
  offer_code: 'rate_term_refi',
  evidence_ids: ['ev-1', 'ev-2'],
  draft_subject: 'email subject',
  draft_body: 'email governed body',
  draft_generation_id: 'gen-email',
  draft_response_hash: 'a'.repeat(64),
  draft_source_refreshed_at: SNAPSHOT,
  channel: 'email',
  assigned_to_email: LO.email,
  follow_up_in_days: 7,
  campaign_id: null,
  variant_name: null,
};

/** SMS: no subject (null, never ''), channel 'sms', no routing chosen. */
const GOLDEN_SMS_APPROVE = {
  offer_code: 'rate_term_refi',
  evidence_ids: ['ev-1', 'ev-2'],
  draft_subject: null,
  draft_body: 'sms governed body',
  draft_generation_id: 'gen-sms',
  draft_response_hash: 'a'.repeat(64),
  draft_source_refreshed_at: SNAPSHOT,
  channel: 'sms',
  assigned_to_email: null,
  follow_up_in_days: null,
  campaign_id: null,
  variant_name: null,
};

const GOLDEN_CAMPAIGN_APPROVE = {
  offer_code: 'rate_term_refi',
  evidence_ids: ['ev-1', 'ev-2'],
  draft_subject: 'email subject',
  draft_body: 'email governed body',
  draft_generation_id: 'gen-email',
  draft_response_hash: 'a'.repeat(64),
  draft_source_refreshed_at: SNAPSHOT,
  channel: 'email',
  assigned_to_email: null,
  follow_up_in_days: null,
  campaign_id: CAMPAIGN_ID,
  variant_name: VARIANT,
};

const GOLDEN_OTHER_REJECT = {
  offer_code: 'rate_term_refi',
  evidence_ids: ['ev-1', 'ev-2'],
  channel: 'email',
  rationale_code: 'other_with_text',
  rationale: 'Borrower asked not to be contacted this quarter.',
  campaign_id: null,
  variant_name: null,
};

/** The second argument of a write call, without the (new) request_id. */
function bodyOf(call: unknown[]): Record<string, unknown> {
  const { request_id: _requestId, ...body } = call[1] as Record<string, unknown>;
  return body;
}
function requestIdOf(call: unknown[]): unknown {
  return (call[1] as Record<string, unknown>).request_id;
}

describe('Offer Orchestrator writes', () => {
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
    apiMocks.borrower.mockResolvedValue(BORROWER);
    apiMocks.recommendOffer.mockResolvedValue(RECOMMENDATION);
    apiMocks.borrowerLifecycle.mockResolvedValue(LIFECYCLE);
    apiMocks.draftOutreach.mockImplementation(
      async (_id: string, channel: OutreachDraftResult['channel'], _signal?: AbortSignal, campaign?: { campaign_id: string; variant_name: string }) =>
        draftFor(channel, campaign),
    );
    apiMocks.salesTeam.mockResolvedValue([LO]);
    apiMocks.approve.mockResolvedValue({ approved: true, audit_event_id: 'audit-1', approval_id: 'approval-1' });
    apiMocks.reject.mockResolvedValue({ rejected: true, audit_event_id: 'audit-reject-1' });
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
  });

  function mount(entry = `/offer-orchestrator/${ID}`) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
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

  const reviewBody = () => container.querySelector<HTMLElement>('[data-testid="outreach-draft"]');
  const loaded = (channel = 'email') => {
    const body = reviewBody();
    return body !== null && body.getAttribute('aria-disabled') !== 'true' && body.textContent === `${channel} governed body`;
  };
  function findButton(text: string): HTMLButtonElement | undefined {
    return [...container.querySelectorAll<HTMLButtonElement>('button')].find(
      (candidate) => candidate.textContent?.trim() === text,
    );
  }
  function button(text: string): HTMLButtonElement {
    const match = findButton(text);
    if (!match) throw new Error(`button not found: ${text}`);
    return match;
  }
  function choose(select: HTMLSelectElement, value: string) {
    act(() => {
      select.value = value;
      select.dispatchEvent(new Event('change', { bubbles: true }));
    });
  }
  async function openRejectReview() {
    act(() => button('Reject').click());
    await waitUntil(() => findButton('Confirm reject') !== undefined);
  }

  it('approve (email, routed) sends exactly the pre-port body', async () => {
    mount();
    await waitUntil(() => loaded() && container.querySelector(`option[value="${LO.email}"]`) !== null);
    choose(container.querySelector<HTMLSelectElement>('#lo-assign')!, LO.email);
    choose(container.querySelector<HTMLSelectElement>('#lo-followup')!, '7');
    await act(async () => {
      button('Approve outreach').click();
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    expect(apiMocks.approve.mock.calls[0][0]).toBe(ID);
    expect(bodyOf(apiMocks.approve.mock.calls[0])).toEqual(GOLDEN_EMAIL_APPROVE);
    expect(typeof requestIdOf(apiMocks.approve.mock.calls[0])).toBe('string');
    // No AbortSignal: an approve is never cancelled.
    expect(apiMocks.approve.mock.calls[0]).toHaveLength(2);
    // The on-screen draft was approved: no second /draft was generated for it.
    expect(apiMocks.draftOutreach).toHaveBeenCalledTimes(1);
  });

  it('approve (SMS) sends draft_subject null and channel sms, and generates no extra draft', async () => {
    mount();
    await waitUntil(() => loaded());
    act(() => button('SMS').click());
    await waitUntil(() => loaded('sms'));
    const draftsBefore = apiMocks.draftOutreach.mock.calls.length;
    await act(async () => {
      button('Approve outreach').click();
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    expect(bodyOf(apiMocks.approve.mock.calls[0])).toEqual(GOLDEN_SMS_APPROVE);
    expect(apiMocks.draftOutreach.mock.calls.length).toBe(draftsBefore);
  });

  it('approve (campaign-bound email) carries the campaign binding', async () => {
    mount(`/offer-orchestrator/${ID}?campaign_id=${CAMPAIGN_ID}&variant_name=${encodeURIComponent(VARIANT)}`);
    await waitUntil(() => loaded());
    await act(async () => {
      button('Approve outreach').click();
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 1);
    expect(bodyOf(apiMocks.approve.mock.calls[0])).toEqual(GOLDEN_CAMPAIGN_APPROVE);
  });

  it('reject with Other sends exactly the pre-port body', async () => {
    mount();
    await waitUntil(() => loaded());
    await openRejectReview();
    const form = container.querySelector('[data-testid="offer-action-bar"] form')!;
    choose(form.querySelector('select')!, 'other_with_text');
    const note = form.querySelector('textarea')!;
    act(() => {
      const setter = Object.getOwnPropertyDescriptor(HTMLTextAreaElement.prototype, 'value')!.set!;
      setter.call(note, 'Borrower asked not to be contacted this quarter.');
      note.dispatchEvent(new Event('input', { bubbles: true }));
    });
    act(() => button('Confirm reject').click());
    await waitUntil(() => apiMocks.reject.mock.calls.length === 1);
    expect(bodyOf(apiMocks.reject.mock.calls[0])).toEqual(GOLDEN_OTHER_REJECT);
    expect(apiMocks.reject.mock.calls[0]).toHaveLength(2);
  });

  it('reuses the request_id when the identical intent is retried, and mints a new one after any change', async () => {
    apiMocks.approve
      .mockRejectedValueOnce(new Error('Lakebase unavailable'))
      .mockRejectedValueOnce(new Error('Lakebase unavailable'))
      .mockResolvedValue({ approved: true, audit_event_id: 'audit-1', approval_id: 'approval-1' });
    mount();
    await waitUntil(() => loaded());
    await act(async () => {
      button('Approve outreach').click();
    });
    await waitUntil(() => container.textContent?.includes("Couldn't write approval: Lakebase unavailable") === true);
    // Nothing reads "Approved" after a failed write.
    expect(appMocks.approvals[ID]).toBeUndefined();
    await act(async () => {
      button('Approve outreach').click();
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 2);
    await waitUntil(() => findButton('Approve outreach')?.disabled === false);
    const [first, second] = apiMocks.approve.mock.calls;
    expect(requestIdOf(second)).toBe(requestIdOf(first));
    // A changed field (the follow-up reminder) is a new intent.
    choose(container.querySelector<HTMLSelectElement>('#lo-followup')!, '3');
    await act(async () => {
      button('Approve outreach').click();
    });
    await waitUntil(() => apiMocks.approve.mock.calls.length === 3);
    expect(requestIdOf(apiMocks.approve.mock.calls[2])).not.toBe(requestIdOf(first));
    expect(bodyOf(apiMocks.approve.mock.calls[2]).follow_up_in_days).toBe(3);
  });

  it('a double click on Confirm reject sends one POST', async () => {
    let release: (value: unknown) => void = () => undefined;
    apiMocks.reject.mockReturnValue(new Promise((resolve) => {
      release = resolve;
    }));
    mount();
    await waitUntil(() => loaded());
    await openRejectReview();
    const confirm = button('Confirm reject');
    act(() => {
      confirm.click();
      confirm.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(apiMocks.reject).toHaveBeenCalledTimes(1);
    await act(async () => {
      release({ rejected: true, audit_event_id: 'audit-reject-1' });
    });
    await waitUntil(() => appMocks.approvals[ID] === 'rejected');
    expect(apiMocks.reject).toHaveBeenCalledTimes(1);
  });

  it('a double click on Approve sends one POST', async () => {
    let release: (value: unknown) => void = () => undefined;
    apiMocks.approve.mockReturnValue(new Promise((resolve) => {
      release = resolve;
    }));
    mount();
    await waitUntil(() => loaded());
    const approve = button('Approve outreach');
    act(() => {
      approve.click();
      approve.click();
    });
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 50));
    });
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
    await act(async () => {
      release({ approved: true, audit_event_id: 'audit-1', approval_id: 'approval-1' });
    });
    await waitUntil(() => appMocks.approvals[ID] === 'approved');
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
  });

  it('Approve and Confirm reject stay disabled while this open re-reads a hydrated snapshot', async () => {
    mount();
    await waitUntil(() => loaded());
    // Leave and come back: the snapshot is hydrated from the cache while the
    // re-open's audited read is held open.
    await act(async () => {
      void navigate?.('/lead-queue');
    });
    await waitUntil(() => container.querySelector('[data-testid="elsewhere"]') !== null);
    let release: (value: Borrower360) => void = () => undefined;
    apiMocks.borrower.mockImplementationOnce(() => new Promise<Borrower360>((resolve) => {
      release = resolve;
    }));
    await act(async () => {
      void navigate?.(`/offer-orchestrator/${ID}`);
    });
    await waitUntil(() => loaded() && apiMocks.draftOutreach.mock.calls.length === 2);
    // The hydrated snapshot and the new draft are on screen, but approval waits.
    expect(button('Approve outreach').disabled).toBe(true);
    await openRejectReview();
    expect(button('Confirm reject').disabled).toBe(true);
    act(() => button('Confirm reject').click());
    await act(async () => {
      button('Approve outreach').click();
    });
    expect(apiMocks.approve).not.toHaveBeenCalled();
    expect(apiMocks.reject).not.toHaveBeenCalled();
    await act(async () => release(BORROWER));
    await waitUntil(() => findButton('Approve outreach')?.disabled === false);
    expect(button('Confirm reject').disabled).toBe(false);
  });
});
