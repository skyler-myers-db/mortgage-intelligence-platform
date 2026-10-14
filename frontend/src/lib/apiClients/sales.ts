/**
 * Sales operations endpoint clients: the sales team and loan officer roster,
 * assignment lifecycle, approval funnel, lead distribution, dispositions,
 * and the sales performance reads.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
import type { ApiRequest, ApiResponse } from '../../types/api.gen';
import type {
  CallDisposition,
  LeadAssignment,
  LoanOfficer,
  LoanOfficerAssignment,
  LoanOfficerFunnelDetailResponse,
  AssignmentLifecycleStatus,
  AssignmentOutcome,
  AssignmentOutcomeResponse,
  ApprovalFunnelResponse,
  SalesTeamMember,
  SalesAgingLead,
  SalesConversionResponse,
  CampaignPerformanceFunnelResponse,
  SalesOutcomeSummaryResponse,
  SalesStandupResponse,
} from '../../types';
import type { AssignmentResponse, DispositionResponse } from '../apiTypes';
import {
  _newRequestId,
  getJson,
  postJson,
  patchJson,
} from '../apiTransport';

/**
 * The strategies the Lead Queue SENDS (audit wow-power-5 step 1): 'manual'
 * for one loan officer and 'round_robin' for two or more, both allocated
 * round-robin in request order. 'score_balanced' is the Distribution Board's
 * greedy capacity/region fill (step 2): only POST /sales/distribute runs it,
 * only the Board sends it (DistributeLeadsRequest below), and a single assign
 * refuses it.
 */
export type SalesSendStrategy = 'manual' | 'round_robin';

/** The `/api/loan-officers/assignments` body. */
export type AssignLoanOfficerRequest = ApiRequest<'AssignLoanOfficerRequest'>;

/** The assignment create and status-change response. */
export type LoanOfficerAssignmentResponse = ApiResponse<'LoanOfficerAssignmentResponse'>;

/** The `/api/loan-officers/assignments/{id}/status` body. */
export type AssignmentStatusUpdateRequest = ApiRequest<'AssignmentStatusUpdateRequest'>;

/** The `/api/loan-officers/assignments/{id}/outcome` body. */
export type AssignmentOutcomeRequest = ApiRequest<'AssignmentOutcomeRequest'>;

/** The `/api/leads/{id}/assign` body. */
export type AssignLeadRequest = ApiRequest<'AssignLeadRequest'>;

/** One officer's share of a planned score-balanced fill (aggregates only). */
export type DistributionOfficerLoad = ApiResponse<'DistributionOfficerLoad'>;

/** What the Board's greedy fill placed: counts and sums, never a borrower id. */
export type DistributionPreview = ApiResponse<'DistributionPreview'>;

/** The `/api/sales/distribute` response; `preview` on a Board dry run or commit. */
export interface DistributeLeadsResponse {
  assigned_count: number;
  strategy: string;
  assignments: LeadAssignment[];
  per_lo_counts: Record<string, number>;
  audit_event_id?: string | null;
  preview?: DistributionPreview | null;
}

/**
 * The `/api/sales/distribute` body. The Lead Queue sends a SalesSendStrategy
 * with a request id; the Distribution Board sends 'score_balanced' with
 * `dry_run` (and no request id) or with the previewed
 * `expected_plan_fingerprint`.
 */
export type DistributeLeadsRequest = ApiRequest<'DistributeLeadsRequest'>;

/** The `/api/leads/{id}/disposition` body. */
export type DispositionRequest = ApiRequest<'DispositionRequest'>;

export const salesApi = {
  salesTeam: (signal?: AbortSignal) =>
    getJson<SalesTeamMember[]>('/api/sales/team', signal),

  loanOfficers: (signal?: AbortSignal) =>
    getJson<LoanOfficer[]>('/api/loan-officers', signal),

  loanOfficerAssignments: (
    filters: { loanOfficerId?: string; borrowerId?: string; status?: AssignmentLifecycleStatus } = {},
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    if (filters.loanOfficerId) params.set('loan_officer_id', filters.loanOfficerId);
    if (filters.borrowerId) params.set('borrower_id', filters.borrowerId);
    if (filters.status) params.set('status', filters.status);
    const query = params.toString();
    return getJson<LoanOfficerAssignment[]>(
      `/api/loan-officers/assignments${query ? `?${query}` : ''}`,
      signal,
    );
  },

  assignLoanOfficer: (borrowerId: string, loanOfficerId: string, signal?: AbortSignal) =>
    postJson<LoanOfficerAssignmentResponse, AssignLoanOfficerRequest>(
      '/api/loan-officers/assignments',
      { borrower_id: borrowerId, loan_officer_id: loanOfficerId, request_id: _newRequestId() },
      signal,
    ),

  updateAssignmentStatus: (
    assignmentId: string,
    status: AssignmentLifecycleStatus,
    signal?: AbortSignal,
  ) =>
    patchJson<LoanOfficerAssignmentResponse, AssignmentStatusUpdateRequest>(
      `/api/loan-officers/assignments/${encodeURIComponent(assignmentId)}/status`,
      { status, request_id: _newRequestId() },
      signal,
    ),

  /** S6: record the terminal outcome for an actioned assignment. The server
   *  enforces the lifecycle gate (409 outside 'actioned') and audits the
   *  write; no outreach is sent from this path. */
  recordAssignmentOutcome: (
    assignmentId: string,
    outcome: AssignmentOutcome,
    signal?: AbortSignal,
  ) =>
    postJson<AssignmentOutcomeResponse, AssignmentOutcomeRequest>(
      `/api/loan-officers/assignments/${encodeURIComponent(assignmentId)}/outcome`,
      { outcome, request_id: _newRequestId() },
      signal,
    ),

  /** S6: live approval funnel (UC population stages + Lakebase workflow stages). */
  approvalFunnel: (signal?: AbortSignal) =>
    getJson<ApprovalFunnelResponse>('/api/analytics/funnel', signal),

  /** S6: per-loan-officer funnel drill-down. */
  approvalFunnelLoanOfficer: (loanOfficerId: string, signal?: AbortSignal) =>
    getJson<LoanOfficerFunnelDetailResponse>(
      `/api/analytics/funnel/loan-officers/${encodeURIComponent(loanOfficerId)}`,
      signal,
    ),

  /** `requestId`: one id per intent, so a retry of the same assignment replays. */
  assignLead: (
    borrowerId: string,
    assignedToEmail: string,
    strategy: SalesSendStrategy = 'manual',
    signal?: AbortSignal,
    requestId: string = _newRequestId(),
  ) =>
    postJson<AssignmentResponse, AssignLeadRequest>(
      `/api/leads/${encodeURIComponent(borrowerId)}/assign`,
      {
        assigned_to_email: assignedToEmail,
        strategy,
        expires_in_hours: 24,
        request_id: requestId,
      },
      signal,
    ),

  distributeLeads: (
    borrowerIds: string[],
    loEmails: string[],
    strategy: SalesSendStrategy = 'round_robin',
    signal?: AbortSignal,
    requestId: string = _newRequestId(),
  ) =>
    postJson<DistributeLeadsResponse, DistributeLeadsRequest>(
      '/api/sales/distribute',
      {
        borrower_ids: borrowerIds,
        lo_emails: loEmails,
        strategy,
        expires_in_hours: 24,
        request_id: requestId,
      },
      signal,
    ),

  logDisposition: (
    borrowerId: string,
    payload: {
      lo_email: string;
      outcome: CallDisposition['outcome'];
      callback_at?: string | null;
    },
    signal?: AbortSignal,
    requestId: string = _newRequestId(),
  ) =>
    postJson<DispositionResponse, DispositionRequest>(
      `/api/leads/${encodeURIComponent(borrowerId)}/disposition`,
      {
        ...payload,
        request_id: requestId,
      },
      signal,
    ),

  salesAging: (olderThanDays = 7, limit = 100, signal?: AbortSignal) =>
    getJson<SalesAgingLead[]>(`/api/sales/aging?older_than_days=${olderThanDays}&limit=${limit}`, signal),

  salesStandup: (date?: string, signal?: AbortSignal) => {
    const params = new URLSearchParams();
    if (date) params.set('date', date);
    const qs = params.toString();
    return getJson<SalesStandupResponse>(qs ? `/api/sales/standup?${qs}` : '/api/sales/standup', signal);
  },

  salesConversion: (
    fromDate: string,
    toDate: string,
    groupBy: 'lo' | 'cohort' = 'lo',
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    params.set('from', fromDate);
    params.set('to', toDate);
    params.set('groupBy', groupBy);
    return getJson<SalesConversionResponse>(`/api/sales/conversion?${params.toString()}`, signal);
  },

  salesCampaignPerformance: (
    fromDate: string,
    toDate: string,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    params.set('from', fromDate);
    params.set('to', toDate);
    return getJson<CampaignPerformanceFunnelResponse>(
      `/api/sales/campaign-performance?${params.toString()}`,
      signal,
    );
  },

  salesOutcomeSummary: (
    fromDate: string,
    toDate: string,
    signal?: AbortSignal,
  ) => {
    const params = new URLSearchParams();
    params.set('from', fromDate);
    params.set('to', toDate);
    return getJson<SalesOutcomeSummaryResponse>(`/api/sales/outcomes/summary?${params.toString()}`, signal);
  },
};
