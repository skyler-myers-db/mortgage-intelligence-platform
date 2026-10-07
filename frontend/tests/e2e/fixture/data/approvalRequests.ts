/**
 * Maker-checker approval requests (flow-02 / shell-06, D-approval-flow-c;
 * W5d w5-lq-requests-binding).
 *
 *   - `approvalRequestFixtures`: the default, registered in registry.ts: the
 *     audit-free GET /api/outreach/approval-requests answers an empty list
 *     for the scope asked (no request chips anywhere by default).
 *   - `registerApprovalRequestStore`: an in-memory store one spec owns: the
 *     list by scope, create (recording each body; optionally holding the
 *     reply on a RequestGate or answering a scripted failure), withdraw, and
 *     an approve recorder that answers like the server and closes the
 *     request's row when the decision carries its id.
 *   - Sessions: `NON_APPROVER_SESSION` spreads SESSION and overrides only
 *     can_approve / can_access_admin (W5d C7).
 *   - `contractSamples()`: the 2xx bodies, validated against the real models.
 *
 * Synthetic only: masked ids in the production shape, no names or contacts
 * (the requester label is a display role, never an email).
 */
import type { ApproveResult } from '../../../../src/lib/apiTypes';
import type { SessionResponse } from '../../../../src/types';
import type {
  ApprovalRequestBatchView,
  ApprovalRequestCreate,
  ApprovalRequestCreated,
  ApprovalRequestList,
  ApprovalRequestRow,
  ApprovalRequestWithdrawn,
} from '../../../../src/types/approvalRequests';
import type { ContractSample } from '../contractSamples';
import { fixture, json, type FixtureEntry, type FixtureReply, type FixtureRequest, type MockApi } from '../mockApi';
import { LEADS } from './borrowers';
import { RequestGate } from './decisionReceipt';
import { SNAPSHOT_AT } from './reference';
import { SESSION } from './shell';

export const NON_APPROVER_SESSION: SessionResponse = { ...SESSION, can_approve: false, can_access_admin: false };

export const REQUEST_BATCH_ID = '0f8fad5b-d9cb-469f-a165-70867728950e';
export const REQUEST_AUDIT_ID = '7c9e6679-7425-40de-944b-e07cc4c9a0a1';
export const WITHDRAW_AUDIT_ID = '16fd2706-8baf-433b-82eb-8c7fada847da';
export const REQUESTER_LABEL = 'Loan analyst';
export const REQUEST_REASON = 'Rate-sensitive refinance candidates in our footprint.';

/** Pending rows the request holds: the fixture population's 3rd, 4th and 5th. */
export const REQUESTED_IDS: readonly string[] = LEADS.filter((lead) => lead.approval_status === 'pending')
  .slice(1, 4)
  .map((lead) => lead.borrower_id);

function emptyList(scope: string | null): ApprovalRequestList {
  return { scope: scope === 'open' ? 'open' : 'mine', batches: [] };
}

export const approvalRequestFixtures: FixtureEntry[] = [
  fixture<ApprovalRequestList>('GET', '/api/outreach/approval-requests', ({ query }) => json(emptyList(query.get('scope')))),
];

export function batchView(ids: readonly string[], mine: boolean, states: Record<string, ApprovalRequestRow['state']> = {}): ApprovalRequestBatchView {
  return {
    batch_id: REQUEST_BATCH_ID,
    requested_by_display: REQUESTER_LABEL,
    is_mine: mine,
    rationale: REQUEST_REASON,
    created_at: SNAPSHOT_AT,
    requested_by: null,
    rows: ids.map((borrowerId) => ({ borrower_id: borrowerId, state: states[borrowerId] ?? 'open', approval_id: null })),
  };
}

interface ErrorBody {
  detail: string;
}

export interface ApprovalRequestStore {
  creates: ApprovalRequestCreate[];
  withdraws: string[];
  approvals: Array<Record<string, unknown>>;
  lists: string[];
  createGate: RequestGate;
  /** The request on record (null until a create, or seeded). */
  batch: ApprovalRequestBatchView | null;
}

export interface ApprovalRequestStoreOptions {
  /** Hold the create's 200 until `createGate.release()`. */
  holdCreate?: boolean;
  /** Answer the create with this status and detail instead. */
  failCreate?: { status: number; detail: string } | null;
  /** Seed an open request (the approver's side). */
  seed?: ApprovalRequestBatchView | null;
}

export function registerApprovalRequestStore(mockApi: MockApi, options: ApprovalRequestStoreOptions = {}): ApprovalRequestStore {
  const store: ApprovalRequestStore = {
    creates: [],
    withdraws: [],
    approvals: [],
    lists: [],
    createGate: new RequestGate(),
    batch: options.seed ?? null,
  };
  const listFor = (scope: string | null, mine: boolean): ApprovalRequestList => {
    const open = store.batch?.rows.some((row) => row.state === 'open') ?? false;
    const shown = store.batch && (mine || open) ? [{ ...store.batch, is_mine: mine }] : [];
    return { scope: scope === 'open' ? 'open' : 'mine', batches: shown };
  };
  mockApi.register<ApprovalRequestList>('GET', '/api/outreach/approval-requests', ({ query }: FixtureRequest) => {
    const scope = query.get('scope');
    store.lists.push(scope ?? '');
    return json(listFor(scope, scope !== 'open'));
  });
  mockApi.register<ApprovalRequestCreated | ErrorBody>('POST', '/api/outreach/approval-requests', async (request) => {
    const body = request.body as ApprovalRequestCreate;
    store.creates.push(body);
    if (options.holdCreate) await store.createGate.hold();
    if (options.failCreate) {
      return json<ErrorBody>({ detail: options.failCreate.detail }, { status: options.failCreate.status });
    }
    store.batch = batchView(body.borrower_ids, true);
    return json<ApprovalRequestCreated>({
      batch_id: REQUEST_BATCH_ID,
      audit_event_id: REQUEST_AUDIT_ID,
      requested: [...body.borrower_ids],
      skipped: [],
    });
  });
  mockApi.register<ApprovalRequestWithdrawn>('POST', '/api/outreach/approval-requests/:batchId/withdraw', ({ params }) => {
    store.withdraws.push(params.batchId);
    const open = store.batch?.rows.filter((row) => row.state === 'open').length ?? 0;
    if (store.batch) {
      store.batch = { ...store.batch, rows: store.batch.rows.map((row) => (row.state === 'open' ? { ...row, state: 'withdrawn' } : row)) };
    }
    return json<ApprovalRequestWithdrawn>({
      batch_id: params.batchId,
      withdrawn_now: open,
      already_closed: (store.batch?.rows.length ?? 0) - open,
      audit_event_id: open > 0 ? WITHDRAW_AUDIT_ID : null,
    });
  });
  mockApi.register<ApproveResult>('POST', '/api/outreach/approve', (request): FixtureReply<ApproveResult> => {
    const body = (request.body ?? {}) as Record<string, unknown>;
    store.approvals.push(body);
    const borrowerId = String(body.borrower_id ?? '');
    if (store.batch && body.approval_request_batch_id === store.batch.batch_id) {
      store.batch = {
        ...store.batch,
        rows: store.batch.rows.map((row) => (row.borrower_id === borrowerId ? { ...row, state: 'approved' } : row)),
      };
    }
    return json<ApproveResult>({ approved: true, borrower_id: borrowerId, audit_event_id: REQUEST_AUDIT_ID } as ApproveResult);
  });
  return store;
}

export function contractSamples(): ContractSample[] {
  const created: ApprovalRequestCreated = {
    batch_id: REQUEST_BATCH_ID,
    audit_event_id: REQUEST_AUDIT_ID,
    requested: [...REQUESTED_IDS],
    skipped: [{ borrower_id: LEADS[1].borrower_id, reason: 'already_decided' }],
  };
  const withdrawn: ApprovalRequestWithdrawn = {
    batch_id: REQUEST_BATCH_ID, withdrawn_now: 2, already_closed: 1, audit_event_id: WITHDRAW_AUDIT_ID,
  };
  const list = (scope: 'open' | 'mine', batch: ApprovalRequestBatchView): ApprovalRequestList => ({ scope, batches: [batch] });
  return [
    { source: 'emptyList(open)', method: 'GET', pattern: '/api/outreach/approval-requests', query: 'scope=open', body: emptyList('open') },
    { source: 'list(open)', method: 'GET', pattern: '/api/outreach/approval-requests', query: 'scope=open', body: list('open', batchView(REQUESTED_IDS, false)) },
    {
      source: 'list(mine)',
      method: 'GET',
      pattern: '/api/outreach/approval-requests',
      query: 'scope=mine',
      body: list('mine', batchView(REQUESTED_IDS, true, { [REQUESTED_IDS[0]]: 'approved', [REQUESTED_IDS[1]]: 'decided_outside' })),
    },
    { source: 'created', method: 'POST', pattern: '/api/outreach/approval-requests', body: created },
    {
      source: 'withdrawn',
      method: 'POST',
      pattern: '/api/outreach/approval-requests/:batchId/withdraw',
      path: `/api/outreach/approval-requests/${REQUEST_BATCH_ID}/withdraw`,
      body: withdrawn,
    },
  ];
}
