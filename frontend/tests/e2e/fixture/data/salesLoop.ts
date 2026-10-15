/**
 * The W5d sales loop's bodies (w5-sales-loop): the Distribution Board's dry
 * run and commit, the two Undo releases, and a conversion read in which only
 * some officers logged calls. Specs register them per test
 * through mockApi.register (sales-loop.fixture.spec.ts), so the default
 * registry is unchanged; contractSamples() hands every shape to the fixture
 * contract exporter, which validates it against the real FastAPI models.
 *
 * Masked ids come from data/borrowers; loan officers are lo0N@summit.example
 * in the contract samples' commit and release bodies, and the roster's own
 * addresses when a handler echoes the request's lo_emails (and in the
 * exported BOARD_PLANS previews).
 */
import type { DistributeLeadsResponse, DistributionPreview } from '../../../../src/lib/apiClients/sales';
import type {
  AssignmentReleaseResponse,
  DistributionReleaseResponse,
} from '../../../../src/lib/apiClients/salesLoop';
import type { LeadAssignment, SalesConversionResponse, SalesTeamMember } from '../../../../src/types';
import type { SalesWorkloadResponse } from '../../../../src/lib/apiClients/salesWorkload';
import type { ContractSample } from '../contractSamples';
import { fixture, json, type FixtureEntry, type FixtureReply, type MockApi } from '../mockApi';
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

/** Every fixture lead scores the same, so a plan's score sums follow its counts. */
const LEAD_SCORE = 72;

/** One Board plan for two loan officers (25/day then 20/day), in roster order. */
export interface BoardPlan {
  /** Each officer's open book, which the planner measures capacity against. */
  open: readonly [number, number];
  /** The new leads each officer receives. */
  added: readonly [number, number];
  /** Pool leads no officer had room for. */
  unplaced: number;
  /** Candidates a live assignment already holds, so left out of the pool. */
  alreadyAssigned: number;
}

/**
 * Every plan the Board fixture serves. Each is one
 * backend/services/sales_distribution_plan.py produces for its officers on a
 * pool of equal-score, out-of-region leads, and
 * tests/unit/test_e2e_fixture_contract.py replays every one (contractSamples
 * below exports them all) through the real planner:
 *  - rule 2: an officer's room is max(0, capacity - open), so a book already
 *    over capacity (Loan Officer B: 22 open at 20/day) takes no lead. That is
 *    the only plan that draws the over-capacity marker, and it never sits on
 *    a planned lead;
 *  - rule 4: a lead is left unplaced only when NO officer has room.
 * The roster's regions ('Midwest', 'South') are not states, so no lead is
 * in-region.
 */
export const BOARD_PLANS = {
  /** A has room for 19 and takes the whole pool; B is over capacity. */
  default: { open: [6, 22], added: [4, 0], unplaced: 0, alreadyAssigned: 3 },
  /** The re-plan after a 409: one more pool lead was assigned elsewhere meanwhile. */
  moved: { open: [6, 22], added: [3, 0], unplaced: 0, alreadyAssigned: 4 },
  /** Every book full: nothing placed, the whole pool left unplaced. */
  full: { open: [25, 22], added: [0, 0], unplaced: 2, alreadyAssigned: 3 },
} as const satisfies Record<string, BoardPlan>;

export type BoardPlanName = keyof typeof BOARD_PLANS;

/** The named plan across `officers` (two, in roster order). Aggregates only, never a borrower id. */
export function boardPreview(officers: SalesTeamMember[], name: BoardPlanName = 'default', fingerprint = BOARD_FINGERPRINT): DistributionPreview {
  const plan: BoardPlan = BOARD_PLANS[name];
  if (officers.length !== plan.open.length) {
    throw new Error(`board plan ${name} covers ${plan.open.length} loan officers, not ${officers.length}`);
  }
  const loads = officers.map((officer, index) => ({
    email: officer.email,
    display_label: officer.display_label,
    region: officer.region ?? null,
    capacity_per_day: officer.capacity_per_day,
    open_count: plan.open[index],
    added_count: plan.added[index],
    added_score_sum: plan.added[index] * LEAD_SCORE,
    in_region_count: 0,
  }));
  const placed = loads.reduce((sum, load) => sum + load.added_count, 0);
  return {
    strategy: 'score_balanced',
    plan_fingerprint: fingerprint,
    pool_count: placed + plan.unplaced,
    placed_count: placed,
    placed_score_sum: placed * LEAD_SCORE,
    unplaced_no_capacity: plan.unplaced,
    skipped_not_assignable: 1,
    skipped_already_assigned: plan.alreadyAssigned,
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
  options: { plan?: BoardPlanName; onCommit?: (body: DistributeBody, index: number) => FixtureReply | Promise<FixtureReply> } = {},
): DistributeBody[] {
  const bodies: DistributeBody[] = [];
  let commits = 0;
  mockApi.register('POST', '/api/sales/distribute', ({ body }) => {
    const request = body as DistributeBody;
    bodies.push(request);
    const officers = SALES_TEAM.filter((member) => request.lo_emails.includes(member.email));
    const preview = boardPreview(officers, options.plan);
    if (request.dry_run) return json(dryRunBody(preview));
    commits += 1;
    return options.onCommit ? options.onCommit(request, commits) : json(commitBody(officers, preview));
  });
  return bodies;
}

/**
 * GET /sales/conversion when only `withCalls` logged dispositions in the
 * window: conversion() groups call_dispositions, so every other in-scope
 * officer has no row at all (the manager view lists them as '0 calls').
 */
export function conversionWithCalls(withCalls: SalesTeamMember[], from = '2026-07-13', to = '2026-07-14'): SalesConversionResponse {
  return {
    from_date: from,
    to_date: to,
    group_by: 'lo',
    rows: withCalls.map((officer) => ({
      group_key: officer.email,
      calls_attempted: 40,
      contacts_reached: 15,
      callbacks_scheduled: 4,
      applications_started: 3,
      unique_leads_contacted: 32,
      unique_contacts_reached: 12,
      unique_application_starts: 3,
      application_start_rate: 0.094,
    })),
  };
}

function sampled(source: string, pattern: string, body: unknown, path = pattern): ContractSample {
  return { source, method: 'POST', pattern, path, query: '', status: 200, body };
}

/**
 * GET /api/sales/workload (W5e w5-analytics, critic-10 6c): every in-scope
 * loan officer of the fixture roster, with a period and the same span before
 * it. Officer A holds the most open leads, B the stale one.
 */
export function workloadBody(from: string, to: string, previousFrom: string, previousTo: string, officers: SalesTeamMember[] = SALES_TEAM): SalesWorkloadResponse {
  const counts = [
    { open_count: 14, stale_count: 1, assigned_count: 9, worked_count: 7, previous_assigned_count: 6, previous_worked_count: 7 },
    { open_count: 6, stale_count: 2, assigned_count: 4, worked_count: 5, previous_assigned_count: 5, previous_worked_count: 2 },
  ];
  return {
    from_date: from,
    to_date: to,
    previous_from_date: previousFrom,
    previous_to_date: previousTo,
    stale_days: 7,
    rows: officers
      .filter((officer) => officer.role === 'loan_officer')
      .map((officer, index) => ({
        email: officer.email,
        display_label: officer.display_label,
        region: officer.region ?? null,
        capacity_per_day: officer.capacity_per_day,
        ...counts[index % counts.length],
      })),
  };
}

/** The same number of days immediately before `from` (YYYY-MM-DD, UTC arithmetic). */
function previousWindow(from: string, to: string): [string, string] {
  const day = 24 * 60 * 60 * 1000;
  const start = Date.parse(`${from}T00:00:00Z`);
  const span = Math.round((Date.parse(`${to}T00:00:00Z`) - start) / day);
  const previousTo = start - day;
  const iso = (ms: number) => new Date(ms).toISOString().slice(0, 10);
  return [iso(previousTo - span * day), iso(previousTo)];
}

/** The default registry's workload read (fixture/registry.ts). */
export const salesWorkloadFixtures: FixtureEntry[] = [
  fixture('GET', '/api/sales/workload', ({ query }) => {
    const from = query.get('from') ?? '2026-07-13';
    const to = query.get('to') ?? '2026-07-14';
    const [previousFrom, previousTo] = previousWindow(from, to);
    return json<SalesWorkloadResponse>(workloadBody(from, to, previousFrom, previousTo));
  }),
];

/** Every sales-loop body a spec registers, for the fixture contract exporter. */
export function contractSamples(): ContractSample[] {
  const preview = boardPreview(CONTRACT_OFFICERS);
  const loanOfficers = SALES_TEAM.filter((member) => member.role === 'loan_officer');
  // Every plan a spec can serve, on the roster's own officers: the contract
  // test replays each through the real planner.
  const plans = (Object.keys(BOARD_PLANS) as BoardPlanName[]).map((name) =>
    sampled(`dryRunBody(${name})`, '/api/sales/distribute', dryRunBody(boardPreview(loanOfficers, name))));
  const committed = commitBody(CONTRACT_OFFICERS, preview);
  const single = assignmentRow(LEADS[0].borrower_id, CONTRACT_OFFICERS[1]);
  const releasePath = `/api/leads/${LEADS[0].borrower_id}/assignment/release`;
  return [
    sampled('dryRunBody', '/api/sales/distribute', dryRunBody(preview)),
    sampled('commitBody', '/api/sales/distribute', committed),
    ...plans,
    sampled('releaseBody(restored)', '/api/leads/:id/assignment/release', releaseBody(single, CONTRACT_OFFICERS[0]), releasePath),
    sampled('releaseBody(skipped)', '/api/leads/:id/assignment/release', releaseBody(single, null), releasePath),
    sampled('distributionReleaseBody', '/api/sales/distribute/release', distributionReleaseBody(committed.assignments)),
    {
      source: 'conversionWithCalls',
      method: 'GET',
      pattern: '/api/sales/conversion',
      path: '/api/sales/conversion',
      query: 'from=2026-07-13&to=2026-07-14&groupBy=lo',
      status: 200,
      body: conversionWithCalls(CONTRACT_OFFICERS.slice(0, 1)),
    },
    {
      source: 'workloadBody',
      method: 'GET',
      pattern: '/api/sales/workload',
      path: '/api/sales/workload',
      query: 'from=2026-07-08&to=2026-07-14',
      status: 200,
      body: workloadBody('2026-07-08', '2026-07-14', '2026-07-01', '2026-07-07', CONTRACT_OFFICERS),
    },
  ];
}
