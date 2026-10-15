/**
 * @vitest-environment happy-dom
 *
 * Approve / reject wire-code narrowing (audit quality-04 P2, W5c
 * w5-lead-queue-paging cut 4, W5e w5-wire-types A1) at the network layer:
 * the real api client and transport, a real QueryClient and the real
 * TanStack mutations, with only `fetch` stubbed. A code the route would
 * refuse is refused before any request leaves; a reviewed or absent code is
 * sent exactly as before.
 */
import { QueryClient } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OutreachDraftResult } from '../apiTypes';
import { WireCodeRefusedError } from '../outreachWireCodes';
import { useOfferApprove, useOfferReject, type OfferApproveVariables, type OfferRejectVariables } from './offer';
import { useApproveLead, useRejectLead, type ApproveLeadVariables, type RejectLeadVariables } from './outreach';

const BORROWER = 'B-0000000000001';

interface Sent {
  path: string;
  method: string;
  body: Record<string, unknown>;
}

let sent: Sent[];
let draftOfferCode: string | null;
let root: Root;
let client: QueryClient;

function draftBody(): OutreachDraftResult {
  return {
    generation_id: 'gen-1',
    response_hash: 'a'.repeat(64),
    source_refreshed_at: '2026-07-13T12:00:00Z',
    borrower_id: BORROWER,
    offer_code: draftOfferCode,
    channel: 'email',
    subject: 'Your governed mortgage review',
    body: 'draft',
    status: 'draft',
    disclosure_version: 'v1',
  } as OutreachDraftResult;
}

function respond(path: string): Response {
  const body = path.endsWith('/outreach/draft')
    ? draftBody()
    : path.endsWith('/outreach/approve')
      ? { approved: true, audit_event_id: 'audit-approve-1' }
      : { rejected: true, audit_event_id: 'audit-reject-1' };
  return new Response(JSON.stringify(body), { status: 200, headers: { 'Content-Type': 'application/json' } });
}

function posts(suffix: string): Sent[] {
  return sent.filter((request) => request.method === 'POST' && request.path.endsWith(suffix));
}

function approveVars(overrides: Partial<ApproveLeadVariables> = {}): ApproveLeadVariables {
  return {
    decision: 'approve',
    borrowerId: BORROWER,
    requestId: 'req-approve-1',
    reviewedDraft: null,
    campaignBinding: null,
    evidenceIds: ['ev-1'],
    offerCode: 'refi',
    rationale: null,
    bulkId: null,
    bulkRationale: null,
    reviewMode: 'bulk_cohort',
    ...overrides,
  };
}

function rejectVars(overrides: Partial<RejectLeadVariables> = {}): RejectLeadVariables {
  return {
    decision: 'reject',
    borrowerId: BORROWER,
    requestId: 'req-reject-1',
    rationaleCode: 'low_intent',
    rationale: null,
    campaignBinding: null,
    evidenceIds: ['ev-2'],
    offerCode: 'refi',
    bulkId: null,
    ...overrides,
  };
}

function offerApproveVars(offerCode: string | null): OfferApproveVariables {
  return {
    decision: 'approve',
    borrowerId: BORROWER,
    requestId: 'req-offer-approve-1',
    body: {
      offer_code: offerCode,
      evidence_ids: ['ev-001a'],
      draft_subject: 'Review your mortgage options',
      draft_body: 'email governed body',
      draft_generation_id: 'gen-1',
      draft_response_hash: 'a'.repeat(64),
      draft_source_refreshed_at: '2026-07-13T12:00:00Z',
      channel: 'email',
      assigned_to_email: null,
      follow_up_in_days: null,
      campaign_id: null,
      variant_name: null,
      review_mode: 'individual',
    },
  };
}

function offerRejectVars(rationaleCode: string, offerCode: string | null = 'refi'): OfferRejectVariables {
  return {
    decision: 'reject',
    borrowerId: BORROWER,
    requestId: 'req-offer-reject-1',
    body: {
      offer_code: offerCode,
      evidence_ids: ['ev-001a'],
      channel: 'email',
      rationale_code: rationaleCode,
      rationale: null,
      campaign_id: null,
      variant_name: null,
    },
  };
}

function mountHook<T>(useHook: () => T): () => T {
  let value: T | undefined;
  function Probe() {
    value = useHook();
    return null;
  }
  act(() => root.render(createElement(Probe)));
  return () => {
    if (value === undefined) throw new Error('hook did not render');
    return value;
  };
}

async function settle<T>(run: () => Promise<T>): Promise<{ value?: T; error?: unknown }> {
  let outcome: { value?: T; error?: unknown } = {};
  await act(async () => {
    try {
      outcome = { value: await run() };
    } catch (error) {
      outcome = { error };
    }
  });
  return outcome;
}

beforeEach(() => {
  sent = [];
  draftOfferCode = 'refi';
  vi.stubGlobal('fetch', async (path: string, init?: RequestInit) => {
    sent.push({
      path,
      method: init?.method ?? 'GET',
      body: init?.body ? (JSON.parse(String(init.body)) as Record<string, unknown>) : {},
    });
    return respond(path);
  });
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  vi.unstubAllGlobals();
  document.body.innerHTML = '';
});

describe('Lead Queue approve narrowing', () => {
  it('sends a reviewed code exactly as before (non-vacuity control)', async () => {
    const latest = mountHook(() => useApproveLead(client));
    const { value } = await settle(() => latest().mutateAsync(approveVars()));

    expect(value?.approved).toBe(true);
    expect(posts('/outreach/draft')).toHaveLength(1);
    expect(posts('/outreach/approve')).toHaveLength(1);
    expect(posts('/outreach/approve')[0].body.offer_code).toBe('refi');
  });

  it('refuses an unknown row offer code before the draft: 0 POSTs', async () => {
    const latest = mountHook(() => useApproveLead(client));
    const { error } = await settle(() => latest().mutateAsync(approveVars({ offerCode: 'mystery_offer' })));

    expect(error).toBeInstanceOf(WireCodeRefusedError);
    expect((error as WireCodeRefusedError).side).toBe('approve');
    expect(sent.filter((request) => request.method === 'POST')).toHaveLength(0);
  });

  it('refuses an unknown row offer code under a reviewed draft too: 0 POSTs', async () => {
    draftOfferCode = 'refi';
    const latest = mountHook(() => useApproveLead(client));
    const { error } = await settle(() => latest().mutateAsync(approveVars({
      offerCode: 'mystery_offer',
      reviewedDraft: draftBody(),
      reviewMode: 'individual',
    })));

    expect(error).toBeInstanceOf(WireCodeRefusedError);
    expect(sent).toHaveLength(0);
  });

  it('refuses an unknown drafted code before the approve: 1 draft POST, 0 approve POSTs', async () => {
    draftOfferCode = 'mystery_offer';
    const latest = mountHook(() => useApproveLead(client));
    const { error } = await settle(() => latest().mutateAsync(approveVars()));

    expect(error).toBeInstanceOf(WireCodeRefusedError);
    expect(posts('/outreach/draft')).toHaveLength(1);
    expect(posts('/outreach/approve')).toHaveLength(0);
  });

  it('keeps an absent code absent: null, never a substitute', async () => {
    draftOfferCode = null;
    const latest = mountHook(() => useApproveLead(client));
    await settle(() => latest().mutateAsync(approveVars({ offerCode: null })));

    expect(posts('/outreach/approve')).toHaveLength(1);
    expect(posts('/outreach/approve')[0].body.offer_code).toBeNull();
  });
});

describe('Lead Queue reject narrowing', () => {
  it('sends reviewed codes exactly as before (non-vacuity control)', async () => {
    const latest = mountHook(() => useRejectLead(client));
    await settle(() => latest().mutateAsync(rejectVars()));

    expect(posts('/outreach/reject')).toHaveLength(1);
    expect(posts('/outreach/reject')[0].body).toMatchObject({ offer_code: 'refi', rationale_code: 'low_intent' });
  });

  it('refuses an unknown reason: 0 POSTs', async () => {
    const latest = mountHook(() => useRejectLead(client));
    const { error } = await settle(() => latest().mutateAsync(rejectVars({ rationaleCode: 'mystery_reason' })));

    expect(error).toBeInstanceOf(WireCodeRefusedError);
    expect((error as WireCodeRefusedError).field).toBe('rationale_code');
    expect(sent).toHaveLength(0);
  });

  it('refuses an unknown offer code: 0 POSTs', async () => {
    const latest = mountHook(() => useRejectLead(client));
    const { error } = await settle(() => latest().mutateAsync(rejectVars({ offerCode: 'mystery_offer' })));

    expect(error).toBeInstanceOf(WireCodeRefusedError);
    expect(sent).toHaveLength(0);
  });
});

describe('Offer Orchestrator narrowing', () => {
  it('sends reviewed codes exactly as before (non-vacuity control)', async () => {
    const latest = mountHook(() => ({ approve: useOfferApprove(client), reject: useOfferReject(client) }));
    await settle(() => latest().approve.mutateAsync(offerApproveVars('heloc')));
    await settle(() => latest().reject.mutateAsync(offerRejectVars('do_not_call')));

    expect(posts('/outreach/approve')[0].body.offer_code).toBe('heloc');
    expect(posts('/outreach/reject')[0].body.rationale_code).toBe('do_not_call');
    expect(posts('/outreach/draft')).toHaveLength(0);
  });

  it('refuses an unknown approve offer code: 0 POSTs', async () => {
    const latest = mountHook(() => useOfferApprove(client));
    const { error } = await settle(() => latest().mutateAsync(offerApproveVars('mystery_offer')));

    expect(error).toBeInstanceOf(WireCodeRefusedError);
    expect(sent).toHaveLength(0);
  });

  it('refuses an unknown reject reason: 0 POSTs', async () => {
    const latest = mountHook(() => useOfferReject(client));
    const { error } = await settle(() => latest().mutateAsync(offerRejectVars('mystery_reason')));

    expect(error).toBeInstanceOf(WireCodeRefusedError);
    expect(sent).toHaveLength(0);
  });
});
