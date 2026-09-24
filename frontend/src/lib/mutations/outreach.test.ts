/**
 * @vitest-environment happy-dom
 *
 * The governed outreach mutations (audit stack-09 / tables-05 step 2), at
 * the layer the invariants live: a real QueryClient and real TanStack
 * mutation / query machinery, with only the network client mocked.
 */
import { onlineManager, QueryClient, useQuery } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { OutreachDraftResult } from '../apiTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  draftOutreach: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  borrower: vi.fn(),
}));

vi.mock('../api', () => {
  class ApiError extends Error {
    readonly status: number | null;
    readonly aborted: boolean;
    constructor(message: string, opts: { path?: string; status?: number | null; aborted?: boolean } = {}) {
      super(message);
      this.status = opts.status ?? null;
      this.aborted = opts.aborted ?? false;
    }
  }
  return {
    api: apiMocks,
    ApiError,
    isAbortError: (err: unknown) => err instanceof ApiError && err.aborted,
  };
});

import { ApiError } from '../api';
import {
  decisionFailure,
  draftForApproval,
  isDecisionPending,
  outreachMutationKeys,
  useApproveLead,
  usePendingDecisions,
  useRejectLead,
  type ApproveLeadVariables,
  type RejectLeadVariables,
} from './outreach';
import { intentFingerprint, useIntentRequestIds } from './requestIds';

const BORROWER = 'B-AAAAAAAAAAAA1';
const OTHER = 'B-AAAAAAAAAAAA2';

const DRAFT: OutreachDraftResult = {
  generation_id: 'gen-1',
  response_hash: 'a'.repeat(64),
  source_refreshed_at: '2026-07-13T12:00:00Z',
  borrower_id: BORROWER,
  offer_code: 'refi',
  channel: 'email',
  subject: 'Your governed mortgage review',
  body: 'draft',
  status: 'draft',
  disclosure_version: 'v1',
} as OutreachDraftResult;

function approveVars(overrides: Partial<ApproveLeadVariables> = {}): ApproveLeadVariables {
  return {
    decision: 'approve',
    borrowerId: BORROWER,
    requestId: 'req-approve-1',
    reviewedDraft: DRAFT,
    campaignBinding: null,
    evidenceIds: ['ev-1'],
    offerCode: 'refi',
    rationale: null,
    bulkId: null,
    bulkRationale: null,
    ...overrides,
  };
}

function rejectVars(overrides: Partial<RejectLeadVariables> = {}): RejectLeadVariables {
  return {
    decision: 'reject',
    borrowerId: OTHER,
    requestId: 'req-reject-1',
    rationaleCode: 'low_intent',
    rationale: null,
    campaignBinding: null,
    evidenceIds: ['ev-2'],
    offerCode: 'refi',
    ...overrides,
  };
}

interface Deferred<T> {
  promise: Promise<T>;
  resolve: (value: T) => void;
}
function deferred<T>(): Deferred<T> {
  let resolve: (value: T) => void = () => undefined;
  const promise = new Promise<T>((r) => {
    resolve = r;
  });
  return { promise, resolve };
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

let root: Root;
let client: QueryClient;

/** Mount a hook; `latest()` returns its value from the last render. */
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

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  client = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  apiMocks.approve.mockResolvedValue({ approved: true, audit_event_id: 'audit-approve-1' });
  apiMocks.reject.mockResolvedValue({ rejected: true, audit_event_id: 'audit-reject-1' });
  apiMocks.draftOutreach.mockResolvedValue(DRAFT);
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  onlineManager.setOnline(true);
  document.body.innerHTML = '';
});

describe('outreach mutation keys (the W3 queue-place contract)', () => {
  it('keys approve and reject under one prefix', () => {
    expect(outreachMutationKeys).toEqual({
      all: ['mip', 'outreach'],
      approve: ['mip', 'outreach', 'approve'],
      reject: ['mip', 'outreach', 'reject'],
    });
  });

  it('builds approve and reject with the governed options and no shared scope', async () => {
    const latest = mountHook(() => ({ approve: useApproveLead(client), reject: useRejectLead(client) }));
    await act(async () => {
      await latest().approve.mutateAsync(approveVars());
      await latest().reject.mutateAsync(rejectVars());
    });
    const [approve, reject] = client.getMutationCache().getAll();
    for (const mutation of [approve, reject]) {
      expect(mutation.options.networkMode).toBe('always');
      expect(mutation.options.retry).toBe(false);
      expect(mutation.options.scope).toBeUndefined();
      // Pessimistic: nothing runs before the POST returns.
      expect(mutation.options.onMutate).toBeUndefined();
    }
    expect(approve.options.mutationKey).toEqual(outreachMutationKeys.approve);
    expect(reject.options.mutationKey).toEqual(outreachMutationKeys.reject);
  });
});

describe('networkMode always', () => {
  it('fails an approve started offline at once and never fires it on reconnect', async () => {
    onlineManager.setOnline(false);
    apiMocks.approve.mockRejectedValue(new ApiError('Network unreachable', { path: '/api/outreach/approve', status: null }));
    const latest = mountHook(() => useApproveLead(client));

    let outcome = 'pending';
    await act(async () => {
      void latest().mutateAsync(approveVars()).then(
        () => { outcome = 'resolved'; },
        () => { outcome = 'rejected'; },
      );
    });
    await flush();

    // TanStack's default 'online' mode would still be paused here.
    expect(outcome).toBe('rejected');
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);

    await act(async () => {
      onlineManager.setOnline(true);
    });
    await flush();
    // Nothing queued behind the network came back to life.
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
    expect(client.getMutationCache().getAll().every((m) => !m.state.isPaused)).toBe(true);
  });
});

describe('invalidation', () => {
  it('marks operational queries stale without refetching an active borrower read', async () => {
    const borrowerKey = ['mip', 'borrower', BORROWER] as const;
    apiMocks.borrower.mockResolvedValue({ borrower_id: BORROWER });
    let approve: ReturnType<typeof useApproveLead> | null = null;
    function Probe() {
      // A mounted, enabled observer: an ACTIVE refetch would call it again
      // and, in the real app, write a VIEW_BORROWER audit row.
      useQuery({ queryKey: borrowerKey, queryFn: () => apiMocks.borrower(BORROWER), staleTime: 0 }, client);
      approve = useApproveLead(client);
      return null;
    }
    act(() => root.render(createElement(Probe)));
    await flush();
    expect(apiMocks.borrower).toHaveBeenCalledTimes(1);

    await act(async () => {
      await approve!.mutateAsync(approveVars());
    });
    await flush();

    expect(client.getQueryState(borrowerKey)?.isInvalidated).toBe(true);
    expect(apiMocks.borrower).toHaveBeenCalledTimes(1);
  });

  it('skips invalidation for a suppressed bulk row and for approved=false', async () => {
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    const latest = mountHook(() => useApproveLead(client));
    await act(async () => {
      await latest().mutateAsync(approveVars({ suppressInvalidation: true }));
    });
    apiMocks.approve.mockResolvedValue({ approved: false });
    await act(async () => {
      await latest().mutateAsync(approveVars({ requestId: 'req-approve-2' }));
    });
    expect(invalidate).not.toHaveBeenCalled();
  });

  it('never writes the cache directly', async () => {
    const setQueryData = vi.spyOn(client, 'setQueryData');
    const setQueriesData = vi.spyOn(client, 'setQueriesData');
    const latest = mountHook(() => ({ approve: useApproveLead(client), reject: useRejectLead(client) }));
    await act(async () => {
      await latest().approve.mutateAsync(approveVars());
      await latest().reject.mutateAsync(rejectVars());
    });
    expect(setQueryData).not.toHaveBeenCalled();
    expect(setQueriesData).not.toHaveBeenCalled();
  });
});

describe('request bodies', () => {
  it('sends the intent request_id and the reviewed draft, drafting nothing', async () => {
    const latest = mountHook(() => useApproveLead(client));
    await act(async () => {
      await latest().mutateAsync(approveVars({ requestId: 'req-intent-7' }));
    });
    expect(apiMocks.draftOutreach).not.toHaveBeenCalled();
    expect(apiMocks.approve).toHaveBeenCalledWith(
      BORROWER,
      expect.objectContaining({
        request_id: 'req-intent-7',
        draft_generation_id: 'gen-1',
        draft_subject: 'Your governed mortgage review',
        offer_code: 'refi',
      }),
      undefined,
    );
  });

  it('drafts an unsampled bulk row inside the mutation', async () => {
    const latest = mountHook(() => useApproveLead(client));
    await act(async () => {
      await latest().mutateAsync(approveVars({ reviewedDraft: null, bulkId: 'bulk-1', bulkRationale: 'Q3 refi' }));
    });
    expect(apiMocks.draftOutreach).toHaveBeenCalledTimes(1);
    expect(apiMocks.approve).toHaveBeenCalledWith(
      BORROWER,
      expect.objectContaining({ bulk_id: 'bulk-1', bulk_rationale: 'Q3 refi', request_id: 'req-approve-1' }),
      undefined,
    );
  });

  it('sends the reject reason and request_id', async () => {
    const latest = mountHook(() => useRejectLead(client));
    await act(async () => {
      await latest().mutateAsync(rejectVars({ rationaleCode: 'do_not_call', rationale: 'Asked not to be called' }));
    });
    expect(apiMocks.reject).toHaveBeenCalledWith(
      OTHER,
      expect.objectContaining({ rationale_code: 'do_not_call', rationale: 'Asked not to be called', request_id: 'req-reject-1' }),
    );
  });
});

describe('pending decisions from the MutationCache', () => {
  it('names the decision on the wire per borrower and clears it when the POST returns', async () => {
    const approveReply = deferred<{ approved: boolean }>();
    const rejectReply = deferred<{ rejected: boolean }>();
    apiMocks.approve.mockReturnValue(approveReply.promise);
    apiMocks.reject.mockReturnValue(rejectReply.promise);
    const latest = mountHook(() => ({
      approve: useApproveLead(client),
      reject: useRejectLead(client),
      pending: usePendingDecisions(client),
    }));

    await act(async () => {
      void latest().approve.mutateAsync(approveVars());
      void latest().reject.mutateAsync(rejectVars());
    });
    await flush();

    expect([...latest().pending]).toEqual([[BORROWER, 'approve'], [OTHER, 'reject']]);
    expect(isDecisionPending(client, BORROWER)).toBe(true);
    expect(isDecisionPending(client, OTHER)).toBe(true);
    expect(isDecisionPending(client, 'B-NOTONTHEWIRE1')).toBe(false);

    await act(async () => {
      approveReply.resolve({ approved: true });
      rejectReply.resolve({ rejected: true });
    });
    await flush();

    expect(latest().pending.size).toBe(0);
    expect(isDecisionPending(client, BORROWER)).toBe(false);
  });

  it('sees a decision started by a component that has since unmounted', async () => {
    const approveReply = deferred<{ approved: boolean }>();
    apiMocks.approve.mockReturnValue(approveReply.promise);
    const first = mountHook(() => useApproveLead(client));
    await act(async () => {
      void first().mutateAsync(approveVars());
    });
    act(() => root.unmount());
    root = createRoot(document.getElementById('root') as HTMLElement);

    const remounted = mountHook(() => usePendingDecisions(client));
    await flush();

    expect(isDecisionPending(client, BORROWER)).toBe(true);
    expect(remounted().get(BORROWER)).toBe('approve');
    await act(async () => {
      approveReply.resolve({ approved: true });
    });
  });
});

describe('decisionFailure', () => {
  it('maps a cancelled POST, a dropped network and a server refusal', () => {
    expect(decisionFailure(new ApiError('aborted', { path: '/api/outreach/approve', aborted: true }))).toBe('aborted');
    expect(decisionFailure(new ApiError('offline', { path: '/api/outreach/approve', status: null }))).toBe('network');
    expect(decisionFailure(new ApiError('Conflict', { path: '/api/outreach/approve', status: 409 }))).toBe('backend');
    expect(decisionFailure(new Error('stale proof'))).toBe('backend');
  });
});

describe('draftForApproval', () => {
  it('refuses a draft whose campaign variant proof does not match the binding', async () => {
    apiMocks.draftOutreach.mockResolvedValue({ ...DRAFT, campaign_id: 'c-other', variant_name: 'v1' });
    await expect(draftForApproval(BORROWER, { campaign_id: 'c-1', variant_name: 'v1' })).rejects.toThrow(
      'Campaign variant proof is stale',
    );
  });

  it('refuses a draft without a subject', async () => {
    apiMocks.draftOutreach.mockResolvedValue({ ...DRAFT, subject: '  ' });
    await expect(draftForApproval(BORROWER, null)).rejects.toThrow('without a subject');
  });
});

describe('useIntentRequestIds', () => {
  it('reuses the id for the same intent and mints one for a changed intent', () => {
    const latest = mountHook(() => useIntentRequestIds());
    const confirm = intentFingerprint('approve', BORROWER, 'gen-1');
    const first = latest().idFor(confirm);
    expect(latest().idFor(confirm)).toBe(first);
    expect(latest().idFor(intentFingerprint('approve', BORROWER, 'gen-2'))).not.toBe(first);
    latest().settle(confirm);
    expect(latest().idFor(confirm)).not.toBe(first);
  });

  it('fingerprints null and undefined parts alike', () => {
    expect(intentFingerprint('approve', BORROWER, undefined)).toBe(intentFingerprint('approve', BORROWER, null));
  });
});
