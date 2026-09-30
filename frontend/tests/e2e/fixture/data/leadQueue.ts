/**
 * Lead Queue fixtures (wave 4b, lane w4-lead-queue: runtime-04 / runtime-09,
 * tables-01, tables-07, critic-06, critic-08, a11y-v2). Scenario helpers
 * only: nothing here is appended to registry.ts; a spec registers what it
 * proves per test.
 *
 *   - `LEAD_QUEUE_500` / `registerLeadQueue`: a 500-row ranked queue (the
 *     API's default page), the interaction budget's population. The first 24
 *     rows are the default population (their dossiers resolve); the rest are
 *     synthetic masked ids built like data/queueKeyboard.ts `syntheticLead`.
 *   - `registerRejectRecorder`: POST /api/outreach/reject, recording every
 *     body in arrival order, optionally holding the first replies on a
 *     RequestGate or failing chosen ids with a 500.
 *   - `ACTIONED_LEAD` / `registerAssignmentOutcome`: a row whose assignment
 *     is `actioned` (the outcome picker's step) and the outcome POST,
 *     answered with the recorded outcome or a 409 (a stale transition).
 *   - `registerWideFootprint`: GET /api/config/footprint with 24 states, as
 *     wide as live coverage runs, so the STATE filter's menu scrolls (the
 *     default fixture's 8 states barely do).
 *   - `contractSamples()`: the 2xx bodies these helpers answer with, so the
 *     fixture contract validates them against the real response models.
 *
 * Synthetic only: masked ids in the production shape, no names or contacts.
 */
import type { AssignmentOutcome, AssignmentOutcomeResponse, ConfigOptions, LeadSummary } from '../../../../src/types';
import type { RejectResult } from '../../../../src/lib/apiTypes';
import type { ContractSample } from '../contractSamples';
import { json, type FixtureReply, type FixtureRequest, type MockApi } from '../mockApi';
import { LEADS, maskedBorrowerId } from './borrowers';
import { REJECT_AUDIT_ID, RequestGate, rejectResult } from './decisionReceipt';
import { TOTALS } from './reference';
import { GEOGRAPHY_SCOPE } from './shell';

export const LEAD_QUEUE_SIZE = 500;

function syntheticLead(index: number): LeadSummary {
  const template = LEADS[index % LEADS.length];
  const borrowerId = maskedBorrowerId(index);
  return {
    ...template,
    borrower_id: borrowerId,
    display_name: `Owner ${borrowerId.slice(2, 8)}`,
    clip: `clip_demo_${borrowerId.slice(2, 8).toLowerCase()}`,
    opportunity_score: Math.max(40, 52 - Math.floor((index - LEADS.length) / 40)),
    approval_status: 'pending',
    outreach_status: 'none',
  };
}

/** Ranked order, as /api/leads returns its default 500-row page. */
export const LEAD_QUEUE_500: readonly LeadSummary[] = Array.from(
  { length: LEAD_QUEUE_SIZE },
  (_, index) => (index < LEADS.length ? LEADS[index] : syntheticLead(index)),
);

export function registerLeadQueue(mockApi: MockApi, rows: readonly LeadSummary[] = LEAD_QUEUE_500): void {
  mockApi.register<LeadSummary[]>('GET', '/api/leads', () => json<LeadSummary[]>([...rows], {
    headers: {
      'X-Total-Matching': String(Math.max(TOTALS.contactable, rows.length)),
      'X-Returned-Rows': String(rows.length),
    },
  }));
}

export interface RejectBody {
  borrower_id?: string;
  request_id?: string;
  rationale_code?: string;
  rationale?: string | null;
}

export interface RejectRecorderOptions {
  /** Hold the first `holdFirst` reject replies until `gate.release()`. */
  holdFirst?: number;
  /** Answer these ids with a 500. */
  failIds?: readonly string[];
}

export interface RejectRecorder {
  readonly bodies: RejectBody[];
  readonly gate: RequestGate;
}

interface ErrorBody {
  detail: string;
}

export function registerRejectRecorder(mockApi: MockApi, options: RejectRecorderOptions = {}): RejectRecorder {
  const recorder: RejectRecorder = { bodies: [], gate: new RequestGate() };
  const holdFirst = options.holdFirst ?? 0;
  mockApi.register<RejectResult | ErrorBody>('POST', '/api/outreach/reject', async (request: FixtureRequest) => {
    const body = (request.body ?? {}) as RejectBody;
    recorder.bodies.push(body);
    if (recorder.bodies.length <= holdFirst) await recorder.gate.hold();
    if (options.failIds?.includes(body.borrower_id ?? '')) {
      return json<ErrorBody>({ detail: 'Internal Server Error' }, { status: 500 });
    }
    return rejectResult(REJECT_AUDIT_ID);
  });
  return recorder;
}

export const ACTIONED_ASSIGNMENT_ID = 'asg-lq-actioned-0001';
const ASSIGNED_LO_EMAIL = 'lo01@summit-mortgage.example';

/** Fifth ranked row: assigned, and its assignment is `actioned` (an outcome is next). */
export const ACTIONED_LEAD: LeadSummary = {
  ...LEADS[4],
  assigned_to_email: ASSIGNED_LO_EMAIL,
  assigned_to_label: 'Summit LO 01',
  assigned_at: '2026-07-13T09:00:00Z',
  assignment_status: 'actioned',
  assignment_id: ACTIONED_ASSIGNMENT_ID,
};

function outcomeResponse(borrowerId: string, outcome: AssignmentOutcome): AssignmentOutcomeResponse {
  return {
    assignment: {
      assignment_id: ACTIONED_ASSIGNMENT_ID,
      borrower_id: borrowerId,
      loan_officer_id: 'lo-summit-01',
      loan_officer_email: ASSIGNED_LO_EMAIL,
      loan_officer_name: 'Summit LO 01',
      status: 'outcome_recorded',
      assigned_by: 'sales.manager@summit-mortgage.example',
      assigned_at: '2026-07-13T09:00:00Z',
      status_updated_at: '2026-07-14T15:00:04Z',
      released_at: null,
    },
    outcome,
    feedback_id: 'fb-lq-0001',
    audit_event_id: '5e4d3c2b-1a09-4f8e-8d7c-6b5a49382716',
  };
}

export interface OutcomeRecorder {
  readonly bodies: Array<{ outcome?: string; request_id?: string }>;
}

/** POST .../outcome for ACTIONED_LEAD: the recorded outcome, or a 409 when `conflict`. */
export function registerAssignmentOutcome(mockApi: MockApi, options: { conflict?: boolean } = {}): OutcomeRecorder {
  const recorder: OutcomeRecorder = { bodies: [] };
  mockApi.register<AssignmentOutcomeResponse | ErrorBody>(
    'POST',
    '/api/loan-officers/assignments/:assignmentId/outcome',
    (request): FixtureReply<AssignmentOutcomeResponse | ErrorBody> => {
      const body = (request.body ?? {}) as { outcome?: AssignmentOutcome; request_id?: string };
      recorder.bodies.push(body);
      if (options.conflict) {
        return json<ErrorBody>({ detail: 'Illegal transition: the assignment is no longer actioned.' }, { status: 409 });
      }
      return json<AssignmentOutcomeResponse>(outcomeResponse(ACTIONED_LEAD.borrower_id, body.outcome ?? 'success'));
    },
  );
  return recorder;
}

/** Mirror of the un-exported `FootprintPayload` (see data/shell.ts). */
interface FootprintPayload {
  states: Array<{ state_code: string; state_name: string; display_order: number; is_default_state: boolean }>;
  geography_scope: NonNullable<ConfigOptions['geography_scope']> | null;
  using_fallback: boolean;
}

const WIDE_FOOTPRINT_STATES: ReadonlyArray<readonly [code: string, name: string]> = [
  ['AL', 'Alabama'], ['AZ', 'Arizona'], ['CA', 'California'], ['CO', 'Colorado'], ['CT', 'Connecticut'],
  ['FL', 'Florida'], ['GA', 'Georgia'], ['IL', 'Illinois'], ['IN', 'Indiana'], ['KY', 'Kentucky'],
  ['MA', 'Massachusetts'], ['MD', 'Maryland'], ['MI', 'Michigan'], ['MN', 'Minnesota'], ['MO', 'Missouri'],
  ['NC', 'North Carolina'], ['NJ', 'New Jersey'], ['NV', 'Nevada'], ['NY', 'New York'], ['OH', 'Ohio'],
  ['OR', 'Oregon'], ['TN', 'Tennessee'], ['TX', 'Texas'], ['WA', 'Washington'],
];

function wideFootprint(): FootprintPayload {
  return {
    states: WIDE_FOOTPRINT_STATES.map(([code, name], index) => ({
      state_code: code,
      state_name: name,
      display_order: index + 1,
      is_default_state: code === 'IL',
    })),
    geography_scope: { ...GEOGRAPHY_SCOPE, state_count: WIDE_FOOTPRINT_STATES.length },
    using_fallback: false,
  };
}

export function registerWideFootprint(mockApi: MockApi): void {
  mockApi.register<FootprintPayload>('GET', '/api/config/footprint', () => json<FootprintPayload>(wideFootprint()));
}

/** Every 2xx body the helpers above answer with (the exporter's record shape). */
export function contractSamples(): ContractSample[] {
  const outcomePath = `/api/loan-officers/assignments/${ACTIONED_ASSIGNMENT_ID}/outcome`;
  return [
    {
      source: 'data/leadQueue.ts#registerWideFootprint',
      method: 'GET',
      pattern: '/api/config/footprint',
      path: '/api/config/footprint',
      query: '',
      status: 200,
      body: wideFootprint(),
    },
    {
      source: 'data/leadQueue.ts#registerAssignmentOutcome',
      method: 'POST',
      pattern: '/api/loan-officers/assignments/:assignmentId/outcome',
      path: outcomePath,
      query: '',
      status: 200,
      body: outcomeResponse(ACTIONED_LEAD.borrower_id, 'success'),
    },
    {
      source: 'data/leadQueue.ts#registerRejectRecorder',
      method: 'POST',
      pattern: '/api/outreach/reject',
      path: '/api/outreach/reject',
      query: '',
      status: 200,
      body: rejectResult(REJECT_AUDIT_ID).body,
    },
  ];
}
