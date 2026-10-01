import type { ApprovalStatus, OutreachStatus } from '../types';
import type { LeadAssignment } from './loanOfficer';

export interface CallDisposition {
  disposition_id: string;
  borrower_id: string;
  lo_email: string;
  outcome:
    | 'called_no_answer'
    | 'called_left_voicemail'
    | 'connected'
    | 'callback_scheduled'
    | 'application_started'
    | 'not_interested'
    | 'not_now'
    | 'dead';
  attempt_number: number;
  occurred_at: string;
  callback_at?: string | null;
  notes?: string | null;
  audit_event_id?: string | null;
}

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

export interface SalesAgingLead {
  borrower_id: string;
  approval_status: 'approved';
  approved_at: string;
  age_days: number;
  outreach_status: OutreachStatus;
  outreach_at?: string | null;
  assigned_to_email?: string | null;
  latest_disposition_outcome?: string | null;
  latest_disposition_at?: string | null;
}

export interface SalesStandupResponse {
  date: string;
  calls_logged: number;
  contacts_reached: number;
  callbacks_scheduled: number;
  applications_started: number;
  dead_leads: number;
  by_lo: Array<{
    lo_email: string;
    outcomes: Record<string, number>;
    calls_logged: number;
  }>;
}

export interface SalesConversionResponse {
  from_date: string;
  to_date: string;
  group_by: 'lo' | 'cohort';
  rows: Array<{
    group_key: string;
    calls_attempted: number;
    contacts_reached: number;
    callbacks_scheduled: number;
    applications_started: number;
    unique_leads_contacted?: number;
    unique_contacts_reached?: number;
    unique_application_starts?: number;
    application_start_rate: number;
  }>;
}

export interface SalesOutcomeSummaryResponse {
  from_date: string;
  to_date: string;
  total_outcomes: number;
  applications_submitted: number;
  closed_funded: number;
  unique_applications_submitted?: number;
  unique_closed_funded?: number;
  lost_to_competitor: number;
  withdrawn: number;
  not_qualified: number;
  by_source_system: Array<{
    source_system: string;
    outcomes: Record<string, number>;
    total: number;
  }>;
  source_statuses: Array<{
    source_system: string;
    display_name: string;
    status: string;
    configured: boolean;
    outcome_count: number;
  }>;
  by_lo: Array<{
    lo_email: string;
    outcomes: Record<string, number>;
    total: number;
  }>;
  top_competitors: Array<{
    competitor_lender_label: string;
    lost_to_competitor: number;
  }>;
}
