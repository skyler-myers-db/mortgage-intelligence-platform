/**
 * @vitest-environment happy-dom
 *
 * The Offer Orchestrator's governed writes (wave 4b, stack-09 item 1,
 * states-07 item 3) at the layer the invariants live: a real QueryClient and
 * real TanStack mutation machinery, only the network client mocked.
 */
import { onlineManager, QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({ approve: vi.fn(), reject: vi.fn() }));

vi.mock('../api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../api')>()),
  api: apiMocks,
}));

import { ApiError } from '../apiTransport';
import {
  offerMutationKeys,
  useOfferApprove,
  useOfferDraftSave,
  useOfferReject,
  type OfferApproveVariables,
  type OfferRejectVariables,
} from './offer';
import { isDecisionPending, outreachMutationKeys, usePendingDecisions } from './outreach';

const BORROWER = 'B-0000000000001';

function approveVars(): OfferApproveVariables {
  return {
    decision: 'approve',
    borrowerId: BORROWER,
    requestId: 'req-approve-1',
    body: {
      offer_code: 'refi',
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

function rejectVars(): OfferRejectVariables {
  return {
    decision: 'reject',
    borrowerId: BORROWER,
    requestId: 'req-reject-1',
    body: {
      offer_code: 'refi',
      evidence_ids: ['ev-001a'],
      channel: 'email',
      rationale_code: 'low_intent',
      rationale: null,
      campaign_id: null,
      variant_name: null,
    },
  };
}

let root: Root;
let client: QueryClient;

function mountHook<T>(useHook: () => T): () => T {
  let value: T | undefined;
  function Probe() {
    value = useHook();
    return null;
  }
  act(() => root.render(createElement(QueryClientProvider, { client }, createElement(Probe))));
  return () => {
    if (value === undefined) throw new Error('hook did not render');
    return value;
  };
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  apiMocks.approve.mockResolvedValue({ approved: true, audit_event_id: 'audit-approve-1', approval_id: 'apr-1' });
  apiMocks.reject.mockResolvedValue({ rejected: true, audit_event_id: 'audit-reject-1' });
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  onlineManager.setOnline(true);
  document.body.innerHTML = '';
});

describe('Offer write mutations', () => {
  it('reuse the outreach approve / reject keys, with the governed options and no onMutate', async () => {
    const latest = mountHook(() => ({ approve: useOfferApprove(client), reject: useOfferReject(client) }));
    await act(async () => {
      await latest().approve.mutateAsync(approveVars());
      await latest().reject.mutateAsync(rejectVars());
    });
    const [approve, reject] = client.getMutationCache().getAll();
    expect(approve.options.mutationKey).toEqual(outreachMutationKeys.approve);
    expect(reject.options.mutationKey).toEqual(outreachMutationKeys.reject);
    for (const mutation of [approve, reject]) {
      expect(mutation.options.networkMode).toBe('always');
      expect(mutation.options.retry).toBe(false);
      expect(mutation.options.onMutate).toBeUndefined();
      expect(mutation.options.scope).toBeUndefined();
    }
  });

  it('send the body verbatim plus the intent request_id, with no AbortSignal', async () => {
    const latest = mountHook(() => ({ approve: useOfferApprove(client), reject: useOfferReject(client) }));
    await act(async () => {
      await latest().approve.mutateAsync(approveVars());
      await latest().reject.mutateAsync(rejectVars());
    });
    expect(apiMocks.approve).toHaveBeenCalledWith(BORROWER, { ...approveVars().body, request_id: 'req-approve-1' });
    expect(apiMocks.approve.mock.calls[0]).toHaveLength(2);
    // The Offer page approves the on-screen draft: the ledger says 'individual'.
    expect(apiMocks.approve.mock.calls[0][1]).toEqual(expect.objectContaining({ review_mode: 'individual' }));
    expect(apiMocks.reject).toHaveBeenCalledWith(BORROWER, { ...rejectVars().body, request_id: 'req-reject-1' });
    expect(apiMocks.reject.mock.calls[0]).toHaveLength(2);
  });

  it('isDecisionPending and usePendingDecisions see an Offer write on the wire', async () => {
    let release: (value: unknown) => void = () => undefined;
    apiMocks.approve.mockReturnValue(new Promise((resolve) => {
      release = resolve;
    }));
    const latest = mountHook(() => ({ approve: useOfferApprove(client), pending: usePendingDecisions(client) }));
    expect(isDecisionPending(client, BORROWER)).toBe(false);
    let settled: Promise<unknown> = Promise.resolve();
    act(() => {
      settled = latest().approve.mutateAsync(approveVars());
    });
    // Synchronous: the latch holds before any re-render.
    expect(isDecisionPending(client, BORROWER)).toBe(true);
    await flush();
    expect(latest().pending.get(BORROWER)).toBe('approve');
    await act(async () => {
      release({ approved: true, audit_event_id: 'audit-approve-1' });
      await settled;
    });
    await flush();
    expect(isDecisionPending(client, BORROWER)).toBe(false);
    expect(latest().pending.has(BORROWER)).toBe(false);
  });

  it('fail an approve started offline at once and never fire it on reconnect (networkMode always)', async () => {
    onlineManager.setOnline(false);
    apiMocks.approve.mockRejectedValue(new ApiError('Network unreachable', { path: '/api/outreach/approve', status: null }));
    const latest = mountHook(() => useOfferApprove(client));
    let outcome = 'pending';
    await act(async () => {
      void latest().mutateAsync(approveVars()).then(
        () => { outcome = 'resolved'; },
        () => { outcome = 'rejected'; },
      );
    });
    await flush();
    expect(outcome).toBe('rejected');
    await act(async () => {
      onlineManager.setOnline(true);
    });
    await flush();
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
  });

  it('invalidate only after approved / rejected = true, and never write the cache', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const setQueryData = vi.spyOn(client, 'setQueryData');
    const latest = mountHook(() => ({ approve: useOfferApprove(client), reject: useOfferReject(client) }));
    apiMocks.approve.mockResolvedValueOnce({ approved: false });
    apiMocks.reject.mockResolvedValueOnce({ rejected: false });
    await act(async () => {
      await latest().approve.mutateAsync(approveVars());
      await latest().reject.mutateAsync(rejectVars());
    });
    expect(invalidate).not.toHaveBeenCalled();
    await act(async () => {
      await latest().approve.mutateAsync(approveVars());
    });
    expect(invalidate).toHaveBeenCalled();
    for (const call of invalidate.mock.calls) {
      expect(call[0]).toMatchObject({ refetchType: 'none' });
    }
    expect(setQueryData).not.toHaveBeenCalled();
  });

  it('the draft save wraps the given saveDraft under its own key, never retried', async () => {
    const saveDraft = vi.fn().mockRejectedValue(new Error('Lakebase unavailable'));
    const latest = mountHook(() => useOfferDraftSave(saveDraft));
    const input = { borrower_id: BORROWER, generation_id: 'gen-1', response_hash: 'a'.repeat(64) };
    act(() => latest().mutate(input));
    await flush();
    expect(saveDraft).toHaveBeenCalledTimes(1);
    expect(saveDraft).toHaveBeenCalledWith(input);
    expect(latest().error?.message).toBe('Lakebase unavailable');
    const [mutation] = client.getMutationCache().getAll();
    expect(mutation.options.mutationKey).toEqual(offerMutationKeys.draftSave);
    expect(mutation.options.retry).toBe(false);
  });
});
