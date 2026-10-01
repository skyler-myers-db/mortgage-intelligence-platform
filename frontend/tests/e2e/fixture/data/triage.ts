/**
 * Triage deck fixtures (D-approval-flow-a2). Nothing here registers by
 * default: a spec calls `registerTriageWrites` per test, so it owns each
 * write's timing and result.
 *
 *   - POST /api/outreach/draft answers for the REQUESTED borrower with a
 *     distinct generation id (a draft writes a DRAFT_OUTREACH audit row in
 *     the real backend; the deck drafts only on A);
 *   - POST /api/outreach/approve, optionally held on a RequestGate, or
 *     answering 409 once (a stale draft);
 *   - POST /api/outreach/reject, answered at once;
 *   - the Decision receipt's ledger read-back.
 *
 * Synthetic only: masked ids in the production shape, no names or contacts.
 */
import type { DecisionReceipt, OutreachDraftResult } from '../../../../src/lib/apiTypes';
import type { ContractSample } from '../contractSamples';
import { json, type FixtureRequest, type MockApi } from '../mockApi';
import { LEADS, PRIMARY_BORROWER } from './borrowers';
import { APPROVE_AUDIT_ID, REJECT_AUDIT_ID, RequestGate, approveResult, ledgerReceipt, rejectResult } from './decisionReceipt';
import { outreachDraftFor } from './offers';

export interface TriageWrites {
  /** Draft POSTs, by borrower id. */
  readonly drafts: string[];
  /** Approve POST bodies, in arrival order. */
  readonly approvals: Array<{ borrower_id?: string; draft_generation_id?: string; review_mode?: string; bulk_id?: string | null }>;
  /** Reject POST bodies, in arrival order. */
  readonly rejects: Array<{ rationale_code?: string }>;
  /** Holds every approve until released (only with `holdApprove`). */
  readonly approveGate: RequestGate;
}

function requestedBorrowerId(request: FixtureRequest): string {
  const body = request.body as { borrower_id?: unknown } | null;
  return typeof body?.borrower_id === 'string' ? body.borrower_id : PRIMARY_BORROWER.borrower_id;
}

export function triageGeneration(borrowerId: string): string {
  return `gen-triage-${borrowerId.slice(2).toLowerCase()}`;
}

/** The draft the deck's A receives for one borrower. */
export function triageDraft(request: FixtureRequest): OutreachDraftResult {
  const borrowerId = requestedBorrowerId(request);
  return {
    ...outreachDraftFor(request),
    borrower_id: borrowerId,
    generation_id: triageGeneration(borrowerId),
    response_hash: [...borrowerId].map((char) => char.charCodeAt(0).toString(16)).join('').padEnd(64, 'f').slice(0, 64),
    subject: `A quick review of your mortgage options (${borrowerId})`,
  };
}

/** The triage draft body (the exporter's record shape), for the fixture contract. */
export function contractSamples(): ContractSample[] {
  const borrowerId = LEADS[2].borrower_id;
  const body = { borrower_id: borrowerId, channel: 'email' };
  const request: FixtureRequest = {
    method: 'POST',
    path: '/api/outreach/draft',
    url: new URL('http://fixture.example/api/outreach/draft'),
    query: new URLSearchParams(),
    params: {},
    body,
  };
  return [{
    source: 'data/triage.ts#triageDraft',
    method: 'POST',
    pattern: '/api/outreach/draft',
    path: '/api/outreach/draft',
    query: '',
    status: 200,
    body: triageDraft(request),
  }];
}

export function registerTriageWrites(
  mockApi: MockApi,
  { holdApprove = false, conflictOnce = false }: { holdApprove?: boolean; conflictOnce?: boolean } = {},
): TriageWrites {
  const writes: TriageWrites = { drafts: [], approvals: [], rejects: [], approveGate: new RequestGate() };
  let conflicted = false;
  mockApi.register<OutreachDraftResult>('POST', '/api/outreach/draft', (request) => {
    writes.drafts.push(requestedBorrowerId(request));
    return json<OutreachDraftResult>(triageDraft(request));
  });
  mockApi.register<unknown>('POST', '/api/outreach/approve', async (request) => {
    writes.approvals.push(request.body as TriageWrites['approvals'][number]);
    if (conflictOnce && !conflicted) {
      conflicted = true;
      return json({ detail: 'The reviewed draft is no longer current' }, { status: 409 });
    }
    if (holdApprove) await writes.approveGate.hold();
    return approveResult(APPROVE_AUDIT_ID);
  });
  mockApi.register('POST', '/api/outreach/reject', (request) => {
    writes.rejects.push(request.body as TriageWrites['rejects'][number]);
    return rejectResult(REJECT_AUDIT_ID);
  });
  mockApi.register<DecisionReceipt>('GET', '/api/audit/receipt/:id', ({ params }) =>
    json<DecisionReceipt>(ledgerReceipt(
      params.id,
      PRIMARY_BORROWER,
      params.id === REJECT_AUDIT_ID ? 'rejected' : 'approved',
    )),
  );
  return writes;
}
