/**
 * The W5d sales loop's bodies (w5-sales-loop): the Distribution Board's dry
 * run and commit, and the two Undo releases. Specs register them per test
 * through mockApi.register (sales-loop.fixture.spec.ts), so the default
 * registry is unchanged; contractSamples() hands every shape to the fixture
 * contract exporter, which validates it against the real FastAPI models.
 *
 * Masked ids come from data/borrowers; loan officers are lo0N@summit.example
 * in the contract samples, and the roster's own addresses when a handler
 * echoes the request's lo_emails.
 */
import type { DistributeLeadsResponse, DistributionPreview } from '../../../../src/lib/apiClients/sales';
import type {
  AssignmentReleaseResponse,
  DistributionReleaseResponse,
} from '../../../../src/lib/apiClients/salesLoop';
import type { LeadAssignment, SalesTeamMember } from '../../../../src/types';
import type { ContractSample } from '../contractSamples';
import { json, type FixtureReply, type MockApi } from '../mockApi';
import { LEADS } from './borrowers';
import { SALES_TEAM } from './portfolio';
import { SNAPSHOT_AT } from './reference';

export const BOARD_FINGERPRINT = 'a1'.repeat(32);
export const BOARD_FINGERPRINT_MOVED = 'b2'.repeat(32);
export const DISTRIBUTE_AUDIT_ID = '6f0d6d3e-2c1b-4c5e-9a6f-1d2e3f4a5b61';
export const UNASSIGN_AUDIT_ID = '6f0d6d3e-2c1b-4c5e-9a6f-1d2e3f4a5b62';
const MANAGER = 'manager@summit.example';

const CONTRACT_OFFICERS: SalesTeamMember[] = [
  { email: 'lo01@summit.example', display_label: 'Summit LO 01', role: 'loan_officer', region: 'IL', manager_email: MANAGER, capacity_per_day: 25, active: true },
  { email: 'lo02@summit.example', display_label: 'Summit LO 02', role: 'loan_officer', region: 'CA', manager_email: MANAGER, capacity_per_day: 20, active: true },
];

/** One assignment row with every key the backend model declares. */
export function assignmentRow(borrowerId: string, officer: SalesTeamMember, extra: Partial<LeadAssignment> = {}): LeadAssignment {
  return {
    assignment_id: `asg-${borrowerId}-${officer.email.split('@')[0]}`,
    borrower_id: borrowerId,
    assigned_to_email: officer.email,
    assigned_to_label: officer.display_label,
    assigned_by: MANAGER,
    assigned_at: SNAPSHOT_AT,
    expires_at: null,
    released_at: null,
    strategy: 'manual',
    status: 'assigned',
    ...extra,
  };
}

/**
 * A plan across `officers`: the first officer takes `added[0]` new leads, the
 * second `added[1]`, and so on. Aggregates only, never a borrower id.
 */
export function boardPreview(officers: SalesTeamMember[], added: number[], fingerprint = BOARD_FINGERPRINT): DistributionPreview {
  const loads = officers.map((officer, index) => ({
    email: officer.email,
    display_label: officer.display_label,
    region: officer.region ?? null,
    capacity_per_day: officer.capacity_per_day,
    open_count: index === 0 ? 6 : officer.capacity_per_day + 2,
    added_count: added[index] ?? 0,
    added_score_sum: (added[index] ?? 0) * 72,
    in_region_count: Math.max(0, (added[index] ?? 0) - 1),
  }));
  const placed = loads.reduce((sum, load) => sum + load.added_count, 0);
  return {
    strategy: 'score_balanced',
    plan_fingerprint: fingerprint,
    pool_count: placed + 2,
    placed_count: placed,
    placed_score_sum: loads.reduce((sum, load) => sum + load.added_score_sum, 0),
    unplaced_no_capacity: 2,
    skipped_not_assignable: 1,
    skipped_already_assigned: 3,
    officers: loads,
  };
}

/** The dry run's body: nothing assigned, the plan in `preview`. */
export function dryRunBody(preview: DistributionPreview): DistributeLeadsResponse {
  return { assigned_count: 0, strategy: 'score_balanced', assignments: [], per_lo_counts: {}, audit_event_id: null, preview };
}

/** The commit's body: what the plan placed, with its LEAD_DISTRIBUTE id. */
export function commitBody(officers: SalesTeamMember[], preview: DistributionPreview): DistributeLeadsResponse {
  const assignments: LeadAssignment[] = [];
  preview.officers.forEach((load) => {
    const officer = officers.find((member) => member.email === load.email) ?? officers[0];
    for (let n = 0; n < load.added_count; n += 1) {
      const lead = LEADS[assignments.length % LEADS.length];
      assignments.push(assignmentRow(lead.borrower_id, officer, { strategy: 'score_balanced', assignment_id: `asg-board-${assignments.length}` }));
    }
  });
  const perOfficer: Record<string, number> = {};
  assignments.forEach((row) => {
    perOfficer[row.assigned_to_email] = (perOfficer[row.assigned_to_email] ?? 0) + 1;
  });
  return {
    assigned_count: assignments.length,
    strategy: 'score_balanced',
    assignments,
    per_lo_counts: perOfficer,
    audit_event_id: DISTRIBUTE_AUDIT_ID,
    preview,
  };
}

/** The single release: released, and handed back when `restoredTo` is given. */
export function releaseBody(released: LeadAssignment, restoredTo: SalesTeamMember | null): AssignmentReleaseResponse {
  return {
    released: { ...released, released_at: SNAPSHOT_AT },
    restored: restoredTo ? assignmentRow(released.borrower_id, restoredTo, { assignment_id: `${released.assignment_id}-restored` }) : null,
    restore_skipped_reason: restoredTo ? null : 'previous_assignee_out_of_scope',
    audit_event_id: UNASSIGN_AUDIT_ID,
  };
}

/** The distribution release: every row released, one already started. */
export function distributionReleaseBody(rows: LeadAssignment[]): DistributionReleaseResponse {
  const released = rows.slice(1).map((row) => ({ ...row, released_at: SNAPSHOT_AT }));
  return {
    released_count: released.length,
    restored_count: 0,
    skipped_started_count: rows.length > 0 ? 1 : 0,
    skipped_not_active_count: 0,
    restore_skipped_count: 0,
    released,
    restored: [],
    audit_event_ids: released.map((_, index) => `6f0d6d3e-2c1b-4c5e-9a6f-1d2e3f4a5c${String(index).padStart(2, '0')}`),
  };
}

/** A distribute POST the app sent (the Board's dry run or commit, or the queue's run). */
export interface DistributeBody {
  borrower_ids: string[];
  lo_emails: string[];
  strategy: string;
  expires_in_hours: number;
  request_id?: string;
  dry_run?: boolean;
  expected_plan_fingerprint?: string;
}

/**
 * Answer the Board's dry run (and, unless `onCommit` says otherwise, its
 * commit) from the request's own loan officers; returns the bodies sent.
 */
export function registerBoard(
  mockApi: MockApi,
  options: { added?: number[]; onCommit?: (body: DistributeBody, index: number) => FixtureReply | Promise<FixtureReply> } = {},
): DistributeBody[] {
  const bodies: DistributeBody[] = [];
  let commits = 0;
  mockApi.register('POST', '/api/sales/distribute', ({ body }) => {
    const request = body as DistributeBody;
    bodies.push(request);
    const officers = SALES_TEAM.filter((member) => request.lo_emails.includes(member.email));
    const preview = boardPreview(officers, options.added ?? [3, 1]);
    if (request.dry_run) return json(dryRunBody(preview));
    commits += 1;
    return options.onCommit ? options.onCommit(request, commits) : json(commitBody(officers, preview));
  });
  return bodies;
}

function sampled(source: string, pattern: string, body: unknown, path = pattern): ContractSample {
  return { source, method: 'POST', pattern, path, query: '', status: 200, body };
}

/** Every sales-loop body a spec registers, for the fixture contract exporter. */
export function contractSamples(): ContractSample[] {
  const preview = boardPreview(CONTRACT_OFFICERS, [3, 1]);
  const committed = commitBody(CONTRACT_OFFICERS, preview);
  const single = assignmentRow(LEADS[0].borrower_id, CONTRACT_OFFICERS[1]);
  const releasePath = `/api/leads/${LEADS[0].borrower_id}/assignment/release`;
  return [
    sampled('dryRunBody', '/api/sales/distribute', dryRunBody(preview)),
    sampled('commitBody', '/api/sales/distribute', committed),
    sampled('releaseBody(restored)', '/api/leads/:id/assignment/release', releaseBody(single, CONTRACT_OFFICERS[0]), releasePath),
    sampled('releaseBody(skipped)', '/api/leads/:id/assignment/release', releaseBody(single, null), releasePath),
    sampled('distributionReleaseBody', '/api/sales/distribute/release', distributionReleaseBody(committed.assignments)),
  ];
}
