/**
 * Sales-operations fixtures behind the Analytics "Approval funnel" and
 * "Sales ops" tabs: the live approval funnel, loan-officer roster, aging
 * approved leads, the daily standup, conversion and recorded outcomes.
 *
 * Loan officers are role labels (`Loan Officer A`), never invented people.
 */
import type {
  ApprovalFunnelResponse,
  LoanOfficer,
  LoanOfficerAssignment,
  LoanOfficerFunnelDetailResponse,
  LoanOfficerFunnelRow,
  SalesAgingLead,
  SalesConversionResponse,
  SalesOutcomeSummaryResponse,
  SalesStandupResponse,
} from '../../../../src/types';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { LEADS } from './borrowers';
import { SNAPSHOT_AT, SNAPSHOT_DATE, TOTALS } from './reference';

const OFFICERS: LoanOfficerFunnelRow[] = [
  { loan_officer_id: 'lo-alpha', display_name: 'Loan Officer A', email: 'lo.alpha@summit.example', assigned: 18, contact_drafted: 12, approved: 9, actioned: 6, outcome_recorded: 3, total_active: 48 },
  { loan_officer_id: 'lo-bravo', display_name: 'Loan Officer B', email: 'lo.bravo@summit.example', assigned: 14, contact_drafted: 9, approved: 7, actioned: 4, outcome_recorded: 2, total_active: 36 },
];

const LOAN_OFFICERS: LoanOfficer[] = OFFICERS.map((officer, index) => ({
  loan_officer_id: officer.loan_officer_id,
  email: officer.email,
  display_name: officer.display_name,
  coverage_states: index === 0 ? ['IL', 'CO', 'WA'] : ['TX', 'FL', 'GA'],
  coverage_counties: [],
  active: true,
}));

const APPROVAL_FUNNEL: ApprovalFunnelResponse = {
  generated_at: SNAPSHOT_AT,
  stages: [
    { stage: 'population', stage_order: 1, label: 'Marketable population', borrower_count: TOTALS.addressable, source: 'mip.gold.borrower_360' },
    { stage: 'high_opportunity', stage_order: 2, label: 'High opportunity', borrower_count: TOTALS.highOpportunity, source: 'mip.gold.borrower_360' },
    { stage: 'approved', stage_order: 3, label: 'Approved', borrower_count: TOTALS.approved, source: 'mip_app.approvals' },
    { stage: 'actioned', stage_order: 4, label: 'Actioned', borrower_count: TOTALS.actioned, source: 'mip_app.lead_assignments' },
    { stage: 'outcome_recorded', stage_order: 5, label: 'Outcome recorded', borrower_count: 96, source: 'mip_app.assignment_outcomes' },
  ],
  approvals: LEADS.slice(0, 6).map((lead, index) => ({
    approval_id: `apr-fixture-${String(index).padStart(4, '0')}`,
    borrower_id: lead.borrower_id,
    offer_code: lead.recommended_offer_code ?? null,
    actor_email: 'approver@summit.example',
    assigned_to_email: OFFICERS[index % OFFICERS.length].email,
    decided_at: `2026-07-14T1${index}:05:00Z`,
  })),
  loan_officers: OFFICERS,
};

function assignmentsFor(officer: LoanOfficerFunnelRow): LoanOfficerAssignment[] {
  return LEADS.slice(0, 4).map((lead, index) => ({
    assignment_id: `asg-${officer.loan_officer_id}-${index}`,
    borrower_id: lead.borrower_id,
    loan_officer_id: officer.loan_officer_id,
    loan_officer_email: officer.email,
    loan_officer_name: officer.display_name,
    status: (['assigned', 'contact_drafted', 'approved', 'actioned'] as const)[index],
    assigned_by: 'manager@summit.example',
    assigned_at: SNAPSHOT_AT,
    status_updated_at: SNAPSHOT_AT,
    released_at: null,
  }));
}

const AGING: SalesAgingLead[] = LEADS.slice(0, 5).map((lead, index) => ({
  borrower_id: lead.borrower_id,
  approval_status: 'approved',
  approved_at: `2026-07-0${index + 1}T15:00:00Z`,
  age_days: 13 - index,
  outreach_status: 'queued',
  outreach_at: null,
  assigned_to_email: OFFICERS[index % OFFICERS.length].email,
  latest_disposition_outcome: null,
  latest_disposition_at: null,
}));

export const salesOpsFixtures: FixtureEntry[] = [
  fixture('GET', '/api/analytics/funnel', () => json<ApprovalFunnelResponse>(APPROVAL_FUNNEL)),
  fixture('GET', '/api/analytics/funnel/loan-officers/:loanOfficerId', ({ params }) => {
    const officer = OFFICERS.find((row) => row.loan_officer_id === params.loanOfficerId) ?? OFFICERS[0];
    return json<LoanOfficerFunnelDetailResponse>({
      officer,
      assignments: assignmentsFor(officer),
      outcome_counts: { success: 2, no_response: 1, declined: 0 },
    });
  }),
  fixture('GET', '/api/loan-officers', () => json<LoanOfficer[]>(LOAN_OFFICERS)),
  fixture('GET', '/api/loan-officers/assignments', () => json<LoanOfficerAssignment[]>([])),
  fixture('GET', '/api/sales/aging', () => json<SalesAgingLead[]>(AGING)),
  fixture('GET', '/api/sales/standup', ({ query }) =>
    json<SalesStandupResponse>({
      date: query.get('date') ?? SNAPSHOT_DATE,
      calls_logged: 42,
      contacts_reached: 17,
      callbacks_scheduled: 6,
      applications_started: 3,
      dead_leads: 2,
      by_lo: OFFICERS.map((officer, index) => ({
        lo_email: officer.email,
        outcomes: { connected: 9 - index * 2, called_no_answer: 11 - index, callback_scheduled: 3 },
        calls_logged: 23 - index * 4,
      })),
    }),
  ),
  fixture('GET', '/api/sales/conversion', ({ query }) =>
    json<SalesConversionResponse>({
      from_date: query.get('from') ?? '2026-07-08',
      to_date: query.get('to') ?? SNAPSHOT_DATE,
      group_by: query.get('groupBy') === 'cohort' ? 'cohort' : 'lo',
      rows: OFFICERS.map((officer, index) => ({
        group_key: officer.email,
        calls_attempted: 120 - index * 20,
        contacts_reached: 48 - index * 9,
        callbacks_scheduled: 14 - index * 3,
        applications_started: 9 - index * 2,
        unique_leads_contacted: 96 - index * 16,
        unique_contacts_reached: 41 - index * 8,
        unique_application_starts: 8 - index * 2,
        application_start_rate: index === 0 ? 0.083 : 0.075,
      })),
    }),
  ),
  fixture('GET', '/api/sales/outcomes/summary', ({ query }) =>
    json<SalesOutcomeSummaryResponse>({
      from_date: query.get('from') ?? '2026-07-08',
      to_date: query.get('to') ?? SNAPSHOT_DATE,
      total_outcomes: 31,
      applications_submitted: 12,
      closed_funded: 5,
      unique_applications_submitted: 11,
      unique_closed_funded: 5,
      lost_to_competitor: 6,
      withdrawn: 4,
      not_qualified: 4,
      by_source_system: [{ source_system: 'los_pos', outcomes: { application_submitted: 12, closed_funded: 5 }, total: 17 }],
      source_statuses: [
        { source_system: 'los_pos', display_name: 'LOS / POS', status: 'dry_run', configured: true, outcome_count: 17 },
        { source_system: 'servicing', display_name: 'Servicing', status: 'not_configured', configured: false, outcome_count: 0 },
      ],
      by_lo: OFFICERS.map((officer, index) => ({
        lo_email: officer.email,
        outcomes: { application_submitted: 7 - index * 2, closed_funded: 3 - index },
        total: 10 - index * 3,
      })),
      top_competitors: [
        { competitor_lender_label: 'Competitor A', lost_to_competitor: 4 },
        { competitor_lender_label: 'Competitor B', lost_to_competitor: 2 },
      ],
    }),
  ),
];
