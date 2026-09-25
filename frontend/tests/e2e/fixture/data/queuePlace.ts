/**
 * Queue-place fixtures (wave 3, lane w3-queue-place: shell-03, runtime-08,
 * tables-09, tables-07, states-08). Scenario helpers only: nothing here is
 * appended to registry.ts; a spec registers what it proves per test.
 *
 *   - `registerRankedQueue`: GET /api/leads answering a given ranked list
 *     (the 160-row VIRTUAL_QUEUE or the 24-row default), honouring
 *     `approval_status` like the default fixture so a preset narrows it.
 *   - `LO_SESSION`: a listed loan officer's session (SALES_TEAM), for the
 *     "Assigned to me" preset; the registry's session is an admin, for whom
 *     the pill is hidden.
 *   - `registerBulkApprove`: POST /api/outreach/approve per row, optionally
 *     holding the first chunk on a RequestGate, failing chosen ids with a 500
 *     or ending the session (401 {}) on one; records every body in order.
 *   - `contractSamples()`: the bodies these helpers answer with, in the
 *     exporter's record shape, so the fixture contract can validate them.
 *
 * Synthetic only: masked ids in the production shape, no names or contacts.
 */
import type { LeadSummary, SessionResponse } from '../../../../src/types';
import type { ApproveResult } from '../../../../src/lib/apiTypes';
import { json, type FixtureReply, type FixtureRequest, type MockApi } from '../mockApi';
import { APPROVE_AUDIT_ID, RequestGate, approveResult } from './decisionReceipt';
import { SALES_TEAM } from './portfolio';
import { TOTALS } from './reference';

export const LO_EMAIL = SALES_TEAM[0].email;

export const LO_SESSION: SessionResponse = {
  can_access_admin: false,
  can_approve: true,
  actor_email: LO_EMAIL,
};

function rankedPage(rows: readonly LeadSummary[], query: URLSearchParams): FixtureReply<LeadSummary[]> {
  const approval = query.get('approval_status');
  const matched = approval ? rows.filter((lead) => lead.approval_status === approval) : [...rows];
  return json<LeadSummary[]>(matched, {
    headers: {
      'X-Total-Matching': String(approval ? matched.length : TOTALS.contactable),
      'X-Returned-Rows': String(matched.length),
    },
  });
}

export function registerRankedQueue(mockApi: MockApi, rows: readonly LeadSummary[]): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', ({ query }) => rankedPage(rows, query));
}

export interface BulkApproveBody {
  borrower_id?: string;
  request_id?: string;
  bulk_id?: string | null;
  bulk_rationale?: string | null;
}

export interface BulkApproveOptions {
  /** Hold the first `holdFirst` approve replies until `gate.release()`. */
  holdFirst?: number;
  /** Answer these ids with a 500. */
  failIds?: readonly string[];
  /** Answer this id with the proxy's ended-session 401 `{}`. */
  expireOn?: string | null;
}

export interface BulkApproveTracker {
  readonly bodies: BulkApproveBody[];
  readonly gate: RequestGate;
}

interface ErrorBody {
  detail: string;
}

export function registerBulkApprove(mockApi: MockApi, options: BulkApproveOptions = {}): BulkApproveTracker {
  const tracker: BulkApproveTracker = { bodies: [], gate: new RequestGate() };
  const holdFirst = options.holdFirst ?? 0;
  mockApi.register<ApproveResult | ErrorBody | Record<string, never>>('POST', '/api/outreach/approve', async (request: FixtureRequest) => {
    const body = (request.body ?? {}) as BulkApproveBody;
    tracker.bodies.push(body);
    if (tracker.bodies.length <= holdFirst) await tracker.gate.hold();
    const borrowerId = body.borrower_id ?? '';
    if (options.expireOn && borrowerId === options.expireOn) {
      return json<Record<string, never>>({}, { status: 401 });
    }
    if (options.failIds?.includes(borrowerId)) {
      return json<ErrorBody>({ detail: 'Internal Server Error' }, { status: 500 });
    }
    return approveResult(APPROVE_AUDIT_ID);
  });
  return tracker;
}

export interface ContractSample {
  source: string;
  method: 'GET' | 'POST';
  pattern: string;
  path: string;
  query: string;
  status: number;
  body: unknown;
}

/** Every 2xx body the helpers above answer with (the exporter's record shape). */
export function contractSamples(): ContractSample[] {
  return [
    {
      source: 'data/queuePlace.ts#LO_SESSION',
      method: 'GET',
      pattern: '/api/session',
      path: '/api/session',
      query: '',
      status: 200,
      body: LO_SESSION,
    },
    {
      source: 'data/queuePlace.ts#registerBulkApprove',
      method: 'POST',
      pattern: '/api/outreach/approve',
      path: '/api/outreach/approve',
      query: '',
      status: 200,
      body: approveResult(APPROVE_AUDIT_ID).body,
    },
  ];
}
