/**
 * @vitest-environment happy-dom
 *
 * useLeadApprovalActions on the governed outreach mutations (audit stack-09
 * / tables-05 step 2), with a REAL QueryClient shared across mounts the way
 * the app shares one: only the network client is mocked.
 *
 * Pinned here: a table that remounted while an approve is on the wire
 * returns 'duplicate' instead of drafting and approving again; "Confirm
 * again" after a 500 replays the same request_id; the bulk loop keeps at
 * most 3 POSTs in flight and one per borrower; an unmount-aborted run is
 * stashed and flashed on the next mount; a double reject sends one POST.
 */

import { QueryClient } from '@tanstack/react-query';
import { act, useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import type { OutreachDraftResult } from '../../lib/apiTypes';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const apiMocks = vi.hoisted(() => ({
  draftOutreach: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
}));

vi.mock('../../lib/api', () => {
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

import { ApiError } from '../../lib/api';
import { useLeadApprovalActions } from './useLeadApprovalActions';

const IDS = Array.from({ length: 7 }, (_, index) => `B-AAAAAAAAAAAA${index + 1}`);
const BORROWER = IDS[0];

function lead(borrowerId: string): LeadSummary {
  return {
    borrower_id: borrowerId,
    approval_status: 'pending',
    marketing_eligible: true,
    consent_status: 'opt_in',
    dnc: false,
    evidence_ids: ['ev-1'],
    segment_codes: ['itm'],
    recommended_offer_code: 'refi',
  } as unknown as LeadSummary;
}
const LEADS = IDS.map(lead);
/** The rows the harness renders; a test may swap them to model a refetch. */
let harnessLeads: LeadSummary[] = LEADS;

function draft(generationId: string): OutreachDraftResult {
  return {
    subject: 'Your governed mortgage review',
    body: 'draft',
    offer_code: 'refi',
    generation_id: generationId,
    response_hash: 'a'.repeat(64),
    source_refreshed_at: '2026-07-13T12:00:00Z',
  } as OutreachDraftResult;
}

type Actions = ReturnType<typeof useLeadApprovalActions>;
let actions: Actions | null = null;
const setApproval = vi.fn();
const setApprovalError = vi.fn();

function Harness({ client }: { client: QueryClient }) {
  const tableWrapRef = useRef<HTMLDivElement | null>(null);
  const current = useLeadApprovalActions({
    displayLeads: harnessLeads,
    leadsById: new Map(harnessLeads.map((row) => [row.borrower_id, row])),
    approvals: {},
    setApproval,
    queryClient: client,
    campaignBinding: null,
    campaignBindingState: 'absent',
    campaignBindingBlocked: false,
    canApprove: true,
    tableWrapRef,
    setApprovalError,
  });
  useEffect(() => {
    actions = current;
  });
  return <div ref={tableWrapRef} />;
}

/** How many times the leads cache was invalidated (invalidateOperationalQueries marks 8 roots). */
function leadInvalidations(spy: { mock: { calls: unknown[][] } }): number {
  return spy.mock.calls.filter((call) => {
    const key = (call[0] as { queryKey?: readonly unknown[] } | undefined)?.queryKey;
    return key?.[0] === 'mip' && key?.[1] === 'leads';
  }).length;
}

function requestIdOf(call: unknown[]): string {
  return (call[1] as { request_id: string }).request_id;
}

async function flush(): Promise<void> {
  await act(async () => {
    await new Promise((resolve) => window.setTimeout(resolve, 0));
  });
}

describe('useLeadApprovalActions on the outreach mutations', () => {
  let container: HTMLDivElement;
  let root: Root;
  let client: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    window.sessionStorage.clear();
    client = new QueryClient();
    apiMocks.draftOutreach.mockImplementation((borrowerId: string) => Promise.resolve(draft(`gen-${borrowerId}`)));
    apiMocks.approve.mockResolvedValue({ approved: true, audit_event_id: 'audit-1' });
    apiMocks.reject.mockResolvedValue({ rejected: true, audit_event_id: 'audit-2' });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    client.clear();
    actions = null;
    harnessLeads = LEADS;
  });

  function mount() {
    act(() => root.render(<Harness client={client} />));
    if (!actions) throw new Error('hook did not mount');
  }

  function remount() {
    act(() => root.unmount());
    actions = null;
    root = createRoot(container);
    mount();
  }

  it('returns duplicate from a remounted table while the approve is still on the wire', async () => {
    let releaseApprove: (value: unknown) => void = () => undefined;
    apiMocks.approve.mockReturnValueOnce(new Promise((resolve) => {
      releaseApprove = resolve;
    }));
    mount();
    let first: Promise<string> | null = null;
    await act(async () => {
      first = actions!.approveLead(BORROWER, undefined, {}, draft('gen-review'));
    });
    await flush();
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);

    // Navigate away and back: a new table, the SAME QueryClient.
    remount();
    expect(actions!.pendingDecisions.get(BORROWER)).toBe('approve');
    let second: string | undefined;
    await act(async () => {
      second = await actions!.approveLead(BORROWER);
    });

    expect(second).toBe('duplicate');
    expect(apiMocks.draftOutreach).not.toHaveBeenCalled();
    expect(apiMocks.approve).toHaveBeenCalledTimes(1);
    await act(async () => {
      releaseApprove({ approved: true, audit_event_id: 'audit-1' });
      await first;
    });
  });

  it('replays the same request_id when Confirm is pressed again after a 500, and mints one for a new draft', async () => {
    apiMocks.approve
      .mockRejectedValueOnce(new ApiError('Internal Server Error', { path: '/api/outreach/approve', status: 500 }))
      .mockRejectedValueOnce(new ApiError('Internal Server Error', { path: '/api/outreach/approve', status: 500 }));
    mount();
    const reviewed = draft('gen-review-1');
    const outcomes: string[] = [];
    await act(async () => {
      outcomes.push(await actions!.approveLead(BORROWER, undefined, {}, reviewed));
    });
    await act(async () => {
      outcomes.push(await actions!.approveLead(BORROWER, undefined, {}, reviewed));
    });
    await act(async () => {
      outcomes.push(await actions!.approveLead(BORROWER, undefined, {}, draft('gen-review-2')));
    });

    expect(outcomes).toEqual(['backend', 'backend', 'ok']);
    const [firstId, againId, newDraftId] = apiMocks.approve.mock.calls.map(requestIdOf);
    expect(againId).toBe(firstId);
    expect(newDraftId).not.toBe(firstId);
    expect(setApproval).toHaveBeenCalledTimes(1);
    expect(setApproval).toHaveBeenCalledWith(BORROWER, 'approved');
  });

  it('runs a bulk approve with at most 3 POSTs in flight and exactly one per borrower', async () => {
    let inFlight = 0;
    let maxInFlight = 0;
    apiMocks.approve.mockImplementation(async () => {
      inFlight += 1;
      maxInFlight = Math.max(maxInFlight, inFlight);
      await new Promise((resolve) => window.setTimeout(resolve, 5));
      inFlight -= 1;
      return { approved: true, audit_event_id: 'audit-bulk' };
    });
    mount();
    act(() => actions!.toggleSelectAll());
    act(() => actions!.setBulkRationale('Q3 refinance push'));
    await act(async () => {
      await actions!.bulkApprove();
    });

    expect(maxInFlight).toBe(3);
    const approvedIds = apiMocks.approve.mock.calls.map((call) => call[0] as string);
    expect([...approvedIds].sort()).toEqual([...IDS].sort());
    const bodies = apiMocks.approve.mock.calls.map((call) => call[1] as { bulk_id: string; request_id: string });
    expect(new Set(bodies.map((body) => body.bulk_id)).size).toBe(1);
    expect(new Set(bodies.map((body) => body.request_id)).size).toBe(IDS.length);
    // A finished run reports through the run's result; the static toast is
    // only the unmount stash (R5-21).
    expect(actions!.bulkRun.result).toMatchObject({ ok: 7, failed: [], skipped: [], notStarted: [], stopped: false });
    expect(actions!.bulkToast).toBeNull();
    expect(actions!.selectionCount).toBe(0);
  });

  it('Stop after this batch: exactly one batch is sent, nothing is aborted, the rest stay selected and undrafted', async () => {
    const held: Array<() => void> = [];
    const signals: AbortSignal[] = [];
    apiMocks.approve.mockImplementation((_id: string, _body: unknown, signal?: AbortSignal) => {
      if (signal) signals.push(signal);
      return new Promise((resolve) => {
        held.push(() => resolve({ approved: true, audit_event_id: 'audit-bulk' }));
      });
    });
    const invalidate = vi.spyOn(client, 'invalidateQueries');
    mount();
    act(() => actions!.toggleSelectAll());
    act(() => actions!.setBulkRationale('Q3 refinance push'));
    let run: Promise<void> = Promise.resolve();
    await act(async () => {
      run = actions!.bulkApprove();
    });
    await flush();
    expect(apiMocks.approve).toHaveBeenCalledTimes(3);
    expect(actions!.bulkRun.progress).toMatchObject({ total: 7, settled: 0 });

    act(() => actions!.bulkRun.requestStop());
    await act(async () => {
      held.splice(0).forEach((release) => release());
      await run;
    });
    await flush();

    expect(apiMocks.approve).toHaveBeenCalledTimes(3);
    expect(apiMocks.draftOutreach).toHaveBeenCalledTimes(3);
    expect(signals.every((signal) => !signal.aborted)).toBe(true);
    expect(actions!.bulkRun.result?.notStarted).toEqual(IDS.slice(3));
    expect([...actions!.selectedIds].sort()).toEqual(IDS.slice(3));
    // One invalidation for the whole run, not one per row.
    expect(leadInvalidations(invalidate)).toBe(1);
  });

  it('a failed approve is listed with its reason and stays selected', async () => {
    apiMocks.approve.mockImplementation((borrowerId: string) => (borrowerId === IDS[4]
      ? Promise.reject(new ApiError('Draft proof is stale.', { path: '/api/outreach/approve', status: 500 }))
      : Promise.resolve({ approved: true, audit_event_id: 'audit-bulk' })));
    mount();
    act(() => actions!.toggleSelectAll());
    act(() => actions!.setBulkRationale('Q3 refinance push'));
    await act(async () => {
      await actions!.bulkApprove();
    });

    expect(actions!.bulkRun.result?.failed).toEqual([
      { borrowerId: IDS[4], outcome: 'backend', message: 'Draft proof is stale.' },
    ]);
    expect([...actions!.selectedIds]).toEqual([IDS[4]]);
    // Bulk rows report in the run's result, not the table's single alert.
    expect(setApprovalError).not.toHaveBeenCalledWith(expect.stringContaining("Couldn't approve"));
  });

  it('certifies the evidence and offer each row had when the run started, not a refetch mid-run', async () => {
    const held: Array<() => void> = [];
    apiMocks.approve.mockImplementation(() => new Promise((resolve) => {
      held.push(() => resolve({ approved: true, audit_event_id: 'audit-bulk' }));
    }));
    mount();
    act(() => actions!.toggleSelectAll());
    act(() => actions!.setBulkRationale('Q3 refinance push'));
    let run: Promise<void> = Promise.resolve();
    await act(async () => {
      run = actions!.bulkApprove();
    });
    await flush();
    // A refetch lands mid-run with different evidence for a row not sent yet.
    harnessLeads = LEADS.map((row) => (row.borrower_id === IDS[5]
      ? { ...row, evidence_ids: ['ev-refetched'], recommended_offer_code: 'heloc' } as LeadSummary
      : row));
    mount();
    for (let batch = 0; batch < 3; batch += 1) {
      await act(async () => {
        held.splice(0).forEach((release) => release());
      });
      await flush();
    }
    await act(async () => {
      await run;
    });

    const body = apiMocks.approve.mock.calls.find((call) => call[0] === IDS[5])?.[1] as { evidence_ids: string[] };
    expect(body.evidence_ids).toEqual(['ev-1']);
  });

  it('stashes an unmount-aborted run, never re-selects it, and flashes it once on the next mount', async () => {
    apiMocks.approve.mockImplementation((_id: string, _body: unknown, signal?: AbortSignal) => new Promise((_resolve, reject) => {
      signal?.addEventListener('abort', () => reject(new ApiError('aborted', { path: '/api/outreach/approve', aborted: true })));
    }));
    mount();
    act(() => actions!.toggleSelectAll());
    act(() => actions!.setBulkRationale('Q3 refinance push'));
    let run: Promise<void> | null = null;
    await act(async () => {
      run = actions!.bulkApprove();
    });
    await flush();
    expect(apiMocks.approve).toHaveBeenCalledTimes(3);

    act(() => root.unmount());
    await act(async () => {
      await run;
    });
    // The first chunk was cut mid-flight, the rest never started.
    expect(apiMocks.approve).toHaveBeenCalledTimes(3);
    expect(JSON.parse(window.sessionStorage.getItem('mip.bulkApprove.lastCancelled') ?? '{}'))
      .toEqual(expect.objectContaining({ ok: 0, aborted: 7 }));

    actions = null;
    root = createRoot(container);
    mount();
    expect(actions!.bulkToast).toEqual({ ok: 0, fail: 0, network: 0, aborted: 7 });
    // Aborted rows are audit-ambiguous: nothing is re-selected for a retry.
    expect(actions!.selectionCount).toBe(0);
    expect(window.sessionStorage.getItem('mip.bulkApprove.lastCancelled')).toBeNull();
  });

  it('sends one reject POST for a double submit and replays its request_id after a failure', async () => {
    let releaseReject: (value: unknown) => void = () => undefined;
    apiMocks.reject
      .mockRejectedValueOnce(new ApiError('Service Unavailable', { path: '/api/outreach/reject', status: 503 }))
      .mockReturnValueOnce(new Promise((resolve) => {
        releaseReject = resolve;
      }));
    mount();
    let failed: boolean | undefined;
    await act(async () => {
      failed = await actions!.rejectLead(BORROWER, 'low_intent', 'No intent');
    });
    expect(failed).toBe(false);

    let first: Promise<boolean> | null = null;
    let second: boolean | undefined;
    await act(async () => {
      first = actions!.rejectLead(BORROWER, 'low_intent', 'No intent');
      second = await actions!.rejectLead(BORROWER, 'low_intent', 'No intent');
    });
    expect(second).toBe(false);
    expect(apiMocks.reject).toHaveBeenCalledTimes(2);
    const [failedId, retryId] = apiMocks.reject.mock.calls.map(requestIdOf);
    expect(retryId).toBe(failedId);
    await act(async () => {
      releaseReject({ rejected: true, audit_event_id: 'audit-2' });
      expect(await first).toBe(true);
    });
    expect(setApproval).toHaveBeenCalledWith(BORROWER, 'rejected');
  });
});
