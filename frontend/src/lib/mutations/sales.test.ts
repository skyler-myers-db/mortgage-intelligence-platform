/**
 * @vitest-environment happy-dom
 *
 * Sales-operations mutations (audit stack-09, wow-power-5 step 1). The
 * transport's postJson is the only thing mocked, so the request BODIES are
 * the ones the real `api.assignLead` / `api.distributeLeads` /
 * `api.logDisposition` build: the strategy the audit row will record is
 * pinned on the wire, not on a helper below it.
 */
import { onlineManager, QueryClient } from '@tanstack/react-query';
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const postJson = vi.hoisted(() => vi.fn());

vi.mock('../apiTransport', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../apiTransport')>();
  return { ...actual, postJson };
});

import {
  distributionStrategy,
  isSingleAssignment,
  salesMutationKeys,
  useAssignLeads,
  useLogDisposition,
} from './sales';

const LO_A = 'lo.alpha@summit.example';
const LO_B = 'lo.bravo@summit.example';

function assignment(borrowerId: string, email: string) {
  return {
    assignment_id: `asg-${borrowerId}`,
    borrower_id: borrowerId,
    assigned_to_email: email,
    assigned_by: 'manager@summit.example',
    assigned_at: '2026-07-14T15:00:00Z',
    strategy: 'manual',
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
  act(() => root.render(createElement(Probe)));
  return () => {
    if (value === undefined) throw new Error('hook did not render');
    return value;
  };
}

interface PostCall {
  path: string;
  body: Record<string, unknown>;
}
function posts(): PostCall[] {
  return postJson.mock.calls.map(([path, body]) => ({ path: String(path), body: body as Record<string, unknown> }));
}

beforeEach(() => {
  vi.clearAllMocks();
  document.body.innerHTML = '<div id="root"></div>';
  root = createRoot(document.getElementById('root') as HTMLElement);
  client = new QueryClient();
  postJson.mockImplementation((path: string, body: Record<string, unknown>) => {
    if (path.endsWith('/assign')) {
      return Promise.resolve({ assignment: assignment('B-AAAAAAAAAAAA1', String(body.assigned_to_email)), audit_event_id: 'audit-assign-1' });
    }
    if (path === '/api/sales/distribute') {
      const ids = body.borrower_ids as string[];
      const los = body.lo_emails as string[];
      return Promise.resolve({
        assigned_count: ids.length,
        strategy: body.strategy,
        assignments: ids.map((id, index) => assignment(id, los[index % los.length])),
        per_lo_counts: {},
        audit_event_id: 'audit-distribute-1',
      });
    }
    return Promise.resolve({
      disposition: { disposition_id: 'd-1', borrower_id: 'B-AAAAAAAAAAAA1', lo_email: LO_A, outcome: 'connected', occurred_at: '2026-07-14T15:00:00Z' },
      audit_event_id: 'audit-disposition-1',
    });
  });
});

afterEach(() => {
  act(() => root.unmount());
  client.clear();
  onlineManager.setOnline(true);
  document.body.innerHTML = '';
});

describe('distributionStrategy', () => {
  it('names the allocation the store really runs', () => {
    expect(distributionStrategy([LO_A])).toBe('manual');
    expect(distributionStrategy([LO_A, LO_B])).toBe('round_robin');
    expect(distributionStrategy([LO_A, LO_B, 'lo.charlie@summit.example'])).toBe('round_robin');
  });

  it('treats one borrower to one officer as an assignment', () => {
    expect(isSingleAssignment({ borrowerIds: ['B-1'], loEmails: [LO_A] })).toBe(true);
    expect(isSingleAssignment({ borrowerIds: ['B-1', 'B-2'], loEmails: [LO_A] })).toBe(false);
    expect(isSingleAssignment({ borrowerIds: ['B-1'], loEmails: [LO_A, LO_B] })).toBe(false);
  });
});

describe('sales mutation keys', () => {
  it('keys assign, distribute and disposition under the sales prefix', () => {
    expect(salesMutationKeys).toEqual({
      all: ['mip', 'sales'],
      assign: ['mip', 'sales', 'assign'],
      distribute: ['mip', 'sales', 'distribute'],
      disposition: ['mip', 'sales', 'disposition'],
    });
  });
});

describe('useAssignLeads request bodies', () => {
  it('assigns one borrower to the selected officer as manual, with the intent request_id', async () => {
    const latest = mountHook(() => useAssignLeads(client));
    await act(async () => {
      await latest().run({ borrowerIds: ['B-AAAAAAAAAAAA1'], loEmails: [LO_A], requestId: 'req-assign-1' });
    });
    expect(posts()).toEqual([
      {
        path: '/api/leads/B-AAAAAAAAAAAA1/assign',
        body: { assigned_to_email: LO_A, strategy: 'manual', expires_in_hours: 24, request_id: 'req-assign-1' },
      },
    ]);
    expect(client.getMutationCache().getAll()[0].options.mutationKey).toEqual(salesMutationKeys.assign);
  });

  it('distributes several borrowers to ONE officer as manual, never score_balanced', async () => {
    const latest = mountHook(() => useAssignLeads(client));
    await act(async () => {
      await latest().run({ borrowerIds: ['B-1', 'B-2'], loEmails: [LO_A], requestId: 'req-dist-1' });
    });
    expect(posts()).toEqual([
      {
        path: '/api/sales/distribute',
        body: { borrower_ids: ['B-1', 'B-2'], lo_emails: [LO_A], strategy: 'manual', expires_in_hours: 24, request_id: 'req-dist-1' },
      },
    ]);
    expect(client.getMutationCache().getAll()[0].options.mutationKey).toEqual(salesMutationKeys.distribute);
  });

  it('distributes across two officers as round_robin', async () => {
    const latest = mountHook(() => useAssignLeads(client));
    let result: Awaited<ReturnType<ReturnType<typeof useAssignLeads>['run']>> | null = null;
    await act(async () => {
      result = await latest().run({ borrowerIds: ['B-1', 'B-2'], loEmails: [LO_A, LO_B], requestId: 'req-dist-2' });
    });
    expect(posts()[0].body.strategy).toBe('round_robin');
    expect(result).toEqual(expect.objectContaining({ assigned_count: 2, audit_event_id: 'audit-distribute-1' }));
    expect(posts().some((call) => call.body.strategy === 'score_balanced')).toBe(false);
  });

  it('runs every sales write with networkMode always and no retry', async () => {
    const latest = mountHook(() => ({ assign: useAssignLeads(client), log: useLogDisposition(client) }));
    await act(async () => {
      await latest().assign.run({ borrowerIds: ['B-1'], loEmails: [LO_A], requestId: 'r-1' });
      await latest().assign.run({ borrowerIds: ['B-1', 'B-2'], loEmails: [LO_A, LO_B], requestId: 'r-2' });
      await latest().log.mutateAsync({
        borrowerId: 'B-1',
        requestId: 'r-3',
        payload: { lo_email: LO_A, outcome: 'connected', callback_at: null, notes: null },
      });
    });
    const mutations = client.getMutationCache().getAll();
    expect(mutations).toHaveLength(3);
    for (const mutation of mutations) {
      expect(mutation.options.networkMode).toBe('always');
      expect(mutation.options.retry).toBe(false);
      expect(mutation.options.scope).toBeUndefined();
    }
  });
});

describe('useLogDisposition request body', () => {
  it('sends the disposition with the intent request_id', async () => {
    const latest = mountHook(() => useLogDisposition(client));
    await act(async () => {
      await latest().mutateAsync({
        borrowerId: 'B-AAAAAAAAAAAA1',
        requestId: 'req-disp-1',
        payload: { lo_email: LO_A, outcome: 'called_left_voicemail', callback_at: null, notes: 'Left a voicemail' },
      });
    });
    expect(posts()).toEqual([
      {
        path: '/api/leads/B-AAAAAAAAAAAA1/disposition',
        body: { lo_email: LO_A, outcome: 'called_left_voicemail', callback_at: null, notes: 'Left a voicemail', request_id: 'req-disp-1' },
      },
    ]);
    expect(client.getMutationCache().getAll()[0].options.mutationKey).toEqual(salesMutationKeys.disposition);
  });
});
