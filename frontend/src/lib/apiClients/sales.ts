/**
 * Sales operations endpoint clients: the sales team and loan officer roster,
 * assignment lifecycle, approval funnel, lead distribution, dispositions,
 * and the sales performance reads.
 *
 * Members moved verbatim out of `api.ts`, which spreads them back into the
 * exported `api` object in the original order. Consumers import `api` from
 * `../api` — never from this module.
 */
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
    postJson<{ assignment: LoanOfficerAssignment; audit_event_id?: string | null }, {
      borrower_id: string;
      loan_officer_id: string;
      request_id: string;
    }>(
      '/api/loan-officers/assignments',
      { borrower_id: borrowerId, loan_officer_id: loanOfficerId, request_id: _newRequestId() },
      signal,
    ),

  updateAssignmentStatus: (
    assignmentId: string,
    status: AssignmentLifecycleStatus,
    signal?: AbortSignal,
  ) =>
    patchJson<{ assignment: LoanOfficerAssignment; audit_event_id?: string | null }, {
      status: AssignmentLifecycleStatus;
      request_id: string;
    }>(
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
    postJson<AssignmentOutcomeResponse, {
      outcome: AssignmentOutcome;
      request_id: string;
    }>(
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

  assignLead: (
    borrowerId: string,
    assignedToEmail: string,
    strategy: 'manual' | 'round_robin' | 'score_balanced' = 'manual',
    signal?: AbortSignal,
  ) =>
    postJson<AssignmentResponse, {
      assigned_to_email: string;
      strategy: 'manual' | 'round_robin' | 'score_balanced';
      expires_in_hours: number;
      request_id: string;
    }>(
      `/api/leads/${encodeURIComponent(borrowerId)}/assign`,
      {
        assigned_to_email: assignedToEmail,
        strategy,
        expires_in_hours: 24,
        request_id: _newRequestId(),
      },
      signal,
    ),

  distributeLeads: (
    borrowerIds: string[],
    loEmails: string[],
    strategy: 'round_robin' | 'score_balanced' = 'round_robin',
    signal?: AbortSignal,
  ) =>
    postJson<{
      assigned_count: number;
      strategy: string;
      assignments: LeadAssignment[];
      per_lo_counts: Record<string, number>;
      audit_event_id?: string | null;
    }, {
      borrower_ids: string[];
      lo_emails: string[];
      strategy: 'round_robin' | 'score_balanced';
      expires_in_hours: number;
      request_id: string;
    }>(
      '/api/sales/distribute',
      {
        borrower_ids: borrowerIds,
        lo_emails: loEmails,
        strategy,
        expires_in_hours: 24,
        request_id: _newRequestId(),
      },
      signal,
    ),

  logDisposition: (
    borrowerId: string,
    payload: {
      lo_email: string;
      outcome: CallDisposition['outcome'];
      callback_at?: string | null;
      notes?: string | null;
    },
    signal?: AbortSignal,
  ) =>
    postJson<DispositionResponse, {
      lo_email: string;
      outcome: CallDisposition['outcome'];
      callback_at?: string | null;
      notes?: string | null;
      request_id: string;
    }>(
      `/api/leads/${encodeURIComponent(borrowerId)}/disposition`,
      {
        ...payload,
        request_id: _newRequestId(),
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
