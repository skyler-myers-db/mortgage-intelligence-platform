/**
 * Queue keyboard + approve-review fixtures (wave 1c, lane
 * queue-keyboard-review: tables-03, wow-power-4, flow-03, states-06).
 *
 * Nothing here registers by default. A spec registers per test:
 *
 *   - `registerVirtualQueue`: a 160-row Lead Queue, past the table's
 *     virtualization threshold (LEAD_VIRTUALIZATION_THRESHOLD = 120), so a
 *     J / K walk has to bring unrendered rows into view. The first 24 rows
 *     are the default population (their dossiers resolve); the rest are
 *     synthetic masked ids that are only ever walked past, never opened.
 *   - `registerDraftEcho`: POST /api/outreach/draft answering for the
 *     REQUESTED borrower with a distinct generation id, and counting calls
 *     per borrower. The draft writes a DRAFT_OUTREACH audit row in the real
 *     backend, so specs assert it is only ever called on explicit intent.
 *   - `registerHeldDecision`: the approve POST held on a RequestGate and the
 *     ledger read-back the Decision receipt makes after it.
 *
 * Synthetic only: masked ids in the production shape, no names or contacts.
 */
import type { LeadSummary } from '../../../../src/types';
import type { DecisionReceipt, OutreachDraftResult } from '../../../../src/lib/apiTypes';
import { json, type FixtureRequest, type MockApi } from '../mockApi';
import { LEADS, PRIMARY_BORROWER, maskedBorrowerId } from './borrowers';
import { RequestGate, approveResult, ledgerReceipt } from './decisionReceipt';
import { outreachDraftFor } from './offers';
import { TOTALS } from './reference';

export const VIRTUAL_QUEUE_SIZE = 160;

function syntheticLead(index: number): LeadSummary {
  const template = LEADS[index % LEADS.length];
  const borrowerId = maskedBorrowerId(index);
  return {
    ...template,
    borrower_id: borrowerId,
    display_name: `Owner ${borrowerId.slice(2, 8)}`,
    clip: `clip_demo_${borrowerId.slice(2, 8).toLowerCase()}`,
    opportunity_score: Math.max(40, 52 - Math.floor((index - LEADS.length) / 12)),
    approval_status: 'pending',
    outreach_status: 'none',
  };
}

/** Ranked order, as /api/leads returns it. */
export const VIRTUAL_QUEUE: readonly LeadSummary[] = Array.from(
  { length: VIRTUAL_QUEUE_SIZE },
  (_, index) => (index < LEADS.length ? LEADS[index] : syntheticLead(index)),
);

export function registerVirtualQueue(mockApi: MockApi): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>([...VIRTUAL_QUEUE], {
    headers: {
      'X-Total-Matching': String(TOTALS.contactable),
      'X-Returned-Rows': String(VIRTUAL_QUEUE.length),
    },
  }));
}

export interface DraftEcho {
  /** Draft POSTs received, by borrower id. */
  readonly calls: string[];
}

function requestedBorrowerId(request: FixtureRequest): string {
  const body = request.body as { borrower_id?: unknown } | null;
  return typeof body?.borrower_id === 'string' ? body.borrower_id : PRIMARY_BORROWER.borrower_id;
}

export function reviewSubject(borrowerId: string): string {
  return `A quick review of your mortgage options (${borrowerId})`;
}

export function registerDraftEcho(mockApi: MockApi): DraftEcho {
  const echo: DraftEcho = { calls: [] };
  mockApi.register<OutreachDraftResult>('POST', '/api/outreach/draft', (request) => {
    const borrowerId = requestedBorrowerId(request);
    echo.calls.push(borrowerId);
    return json<OutreachDraftResult>({
      ...outreachDraftFor(request),
      borrower_id: borrowerId,
      generation_id: `gen-${borrowerId}`,
      response_hash: `${borrowerId.slice(2).toLowerCase()}`.padEnd(64, 'e'),
      subject: reviewSubject(borrowerId),
    });
  });
  return echo;
}

export interface HeldDecision {
  approveGate: RequestGate;
  /** Approve POST bodies, in arrival order. */
  readonly approvals: Array<{ borrower_id?: string; draft_generation_id?: string; draft_subject?: string }>;
}

export const REVIEW_AUDIT_ID = '7a3b0c1d-2e4f-4a5b-8c6d-7e8f9a0b1c2d';

export function registerHeldDecision(mockApi: MockApi): HeldDecision {
  const held: HeldDecision = { approveGate: new RequestGate(), approvals: [] };
  mockApi.register('POST', '/api/outreach/approve', async (request) => {
    held.approvals.push(request.body as HeldDecision['approvals'][number]);
    await held.approveGate.hold();
    return approveResult(REVIEW_AUDIT_ID);
  });
  mockApi.register<DecisionReceipt>('GET', '/api/audit/receipt/:id', ({ params }) =>
    json<DecisionReceipt>(ledgerReceipt(params.id, PRIMARY_BORROWER, 'approved')),
  );
  return held;
}
