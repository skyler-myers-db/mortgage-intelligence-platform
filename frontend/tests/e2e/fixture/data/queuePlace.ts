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
 *   - `registerDraftForRows`: POST /api/outreach/draft answering the
 *     requested ROW's recommended offer (so a queue of several offers drafts
 *     one response per offer code, as the bulk gate's stratified samples
 *     expect), with a generation id and hash unique to the borrower; records
 *     every borrower drafted, in order (w5-approval-core).
 *   - `contractSamples()`: the bodies these helpers answer with, in the
 *     exporter's record shape, so the fixture contract can validate them.
 *
 * Synthetic only: masked ids in the production shape, no names or contacts.
 */
import type { LeadSummary, SessionResponse } from '../../../../src/types';
import type { ApproveResult, OutreachDraftResult, ReviewMode } from '../../../../src/lib/apiTypes';
import { json, type FixtureReply, type FixtureRequest, type MockApi } from '../mockApi';
import { APPROVE_AUDIT_ID, RequestGate, approveResult } from './decisionReceipt';
import { LEADS } from './borrowers';
import { outreachDraftFor } from './offers';
import { SALES_TEAM } from './portfolio';
import { TOTALS } from './reference';

export const LO_EMAIL = SALES_TEAM[0].email;

export const LO_SESSION: SessionResponse = {
  can_access_admin: false,
  can_approve: true,
  can_read_audit: false,
  presenter_mode: false,
  actor_email: LO_EMAIL,
  refusal_text_capture_enabled: false,
  actor_cache_key: null,
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
  /** The review ledger (D-approval-flow-a1): bulk_sample or bulk_cohort in a run. */
  review_mode?: ReviewMode | null;
  draft_generation_id?: string | null;
}

export interface BulkApproveOptions {
  /** Hold the first `holdFirst` approve replies until `gate.release()`. */
  holdFirst?: number;
  /** Answer these ids with a 500. */
  failIds?: readonly string[];
  /** Answer this id with the proxy's ended-session 401 `{}`. */
  expireOn?: string | null;
  /** Answer these ids with the governed text policy's 422 (a canary refusal). */
  refuseIds?: readonly string[];
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
    if (options.refuseIds?.includes(borrowerId)) {
      return json<ErrorBody>({ detail: 'bulk_rationale failed the governed text policy' }, { status: 422 });
    }
    if (options.failIds?.includes(borrowerId)) {
      return json<ErrorBody>({ detail: 'Internal Server Error' }, { status: 500 });
    }
    return approveResult(APPROVE_AUDIT_ID);
  });
  return tracker;
}

/** A hex digest unique to the borrower (deterministic, no crypto needed). */
function borrowerHex(borrowerId: string, length: number): string {
  const codes = [...borrowerId].map((char) => char.charCodeAt(0).toString(16).padStart(2, '0')).join('');
  return codes.padEnd(length, '0').slice(-length);
}

/** The draft a row's own recommended offer gets: one response per offer code. */
export function draftForRow(row: LeadSummary, request: FixtureRequest): OutreachDraftResult {
  return {
    ...outreachDraftFor(request),
    borrower_id: row.borrower_id,
    offer_code: row.recommended_offer_code ?? 'refi',
    generation_id: `00000000-0000-4000-8000-${borrowerHex(row.borrower_id, 12)}`,
    response_hash: borrowerHex(row.borrower_id, 64),
    subject: `A quick review of your mortgage options (${row.borrower_id})`,
  };
}

export interface RowDrafts {
  /** Draft POSTs received, by borrower id, in arrival order. */
  readonly calls: string[];
}

export function registerDraftForRows(mockApi: MockApi, rows: readonly LeadSummary[]): RowDrafts {
  const drafts: RowDrafts = { calls: [] };
  const byId = new Map(rows.map((row) => [row.borrower_id, row]));
  mockApi.register<OutreachDraftResult>('POST', '/api/outreach/draft', (request) => {
    const body = request.body as { borrower_id?: unknown } | null;
    const borrowerId = typeof body?.borrower_id === 'string' ? body.borrower_id : '';
    drafts.calls.push(borrowerId);
    const row = byId.get(borrowerId) ?? rows[0];
    return json<OutreachDraftResult>(draftForRow(row, request));
  });
  return drafts;
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
    {
      source: 'data/queuePlace.ts#registerDraftForRows',
      method: 'POST',
      pattern: '/api/outreach/draft',
      path: '/api/outreach/draft',
      query: '',
      status: 200,
      body: draftForRow(LEADS[0], {
        method: 'POST',
        path: '/api/outreach/draft',
        url: new URL('http://127.0.0.1/api/outreach/draft'),
        query: new URLSearchParams(),
        params: {},
        body: { borrower_id: LEADS[0].borrower_id, channel: 'email' },
      }),
    },
  ];
}
