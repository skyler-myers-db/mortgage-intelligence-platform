import type { ApiResponse } from './api.gen';
export interface SalesTeamMember {
  email: string;
  display_label: string;
  role: 'loan_officer' | 'sales_manager' | 'admin';
  region?: string | null;
  manager_email?: string | null;
  capacity_per_day: number;
  active: boolean;
}

/** S2 assignment lifecycle, enforced as one-step transitions server-side. */
export type AssignmentLifecycleStatus =
  | 'assigned'
  | 'contact_drafted'
  | 'approved'
  | 'actioned'
  | 'outcome_recorded';

export type LoanOfficer = ApiResponse<'LoanOfficer'>;

export type LoanOfficerAssignment = ApiResponse<'LoanOfficerAssignment'>;

export interface LeadAssignment {
  assignment_id: string;
  borrower_id: string;
  assigned_to_email: string;
  assigned_to_label?: string | null;
  assigned_by: string;
  assigned_at: string;
  expires_at?: string | null;
  released_at?: string | null;
  strategy: 'manual' | 'round_robin' | 'score_balanced';
  status?: AssignmentLifecycleStatus;
}
