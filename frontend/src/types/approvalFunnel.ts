import type { LoanOfficerAssignment } from './loanOfficer';

/** Recorded outcome for an actioned assignment, distinct from call outcomes. */
export type AssignmentOutcome = 'success' | 'no_response' | 'declined';

export interface AssignmentOutcomeResponse {
  assignment: LoanOfficerAssignment;
  outcome: AssignmentOutcome;
  feedback_id: string;
  audit_event_id?: string | null;
}

export type ApprovalFunnelStageName =
  | 'population'
  | 'high_opportunity'
  | 'approved'
  | 'actioned'
  | 'outcome_recorded';

export interface ApprovalFunnelStage {
  stage: ApprovalFunnelStageName;
  stage_order: number;
  label: string;
  borrower_count: number;
  source: string;
}

export interface ApproverActivityRow {
  approval_id: string;
  borrower_id: string;
  offer_code?: string | null;
  actor_email: string;
  assigned_to_email?: string | null;
  decided_at: string;
}

export interface AssignmentOutcomeCounts {
  success: number;
  no_response: number;
  declined: number;
}

export interface LoanOfficerFunnelRow {
  loan_officer_id: string;
  display_name: string;
  email: string;
  assigned: number;
  contact_drafted: number;
  approved: number;
  actioned: number;
  outcome_recorded: number;
  total_active: number;
}

export interface ApprovalFunnelResponse {
  generated_at: string;
  stages: ApprovalFunnelStage[];
  approvals: ApproverActivityRow[];
  loan_officers: LoanOfficerFunnelRow[];
}

export interface LoanOfficerFunnelDetailResponse {
  officer: LoanOfficerFunnelRow;
  assignments: LoanOfficerAssignment[];
  outcome_counts: AssignmentOutcomeCounts;
}
