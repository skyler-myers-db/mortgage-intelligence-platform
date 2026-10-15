import type { ApiResponse } from './api.gen';
import type { ApprovalStatus, OutreachStatus } from '../types';
import type { LeadAssignment } from './loanOfficer';

export type CallDisposition = ApiResponse<'CallDisposition'>;

export interface BorrowerLifecycle {
  borrower_id: string;
  approval_status: ApprovalStatus;
  outreach_status: OutreachStatus;
  approval_id?: string | null;
  /** Audit row the latest decision wrote; the Decision receipt reads it back. */
  audit_event_id?: string | null;
  approved_at?: string | null;
  outreach_at?: string | null;
  synced_at?: string | null;
  assignment?: LeadAssignment | null;
  latest_disposition?: CallDisposition | null;
}

export type SalesAgingLead = ApiResponse<'SalesAgingLead'>;

export type SalesStandupResponse = ApiResponse<'SalesStandupResponse'>;

export type SalesConversionResponse = ApiResponse<'SalesConversionResponse'>;

export type SalesOutcomeSummaryResponse = ApiResponse<'SalesOutcomeSummaryResponse'>;
