import type { LeadSummary, SalesTeamMember } from '../../types';

export interface LeadExportContext {
  generatedAt?: string;
  filters?: string;
  refreshedAt?: string | null;
  rulesVersion?: string | null;
}

export interface LeadTableProps {
  leads: LeadSummary[];
  totalMatching?: number | null;
  truncatedAt?: number | null;
  exportContext?: LeadExportContext;
  salesTeam?: SalesTeamMember[];
}

export type RejectReasonCode =
  | 'out_of_footprint'
  | 'do_not_call'
  | 'opt_out'
  | 'fair_lending_review'
  | 'low_intent'
  | 'data_quality'
  | 'other_with_text';

export type SortKey =
  | 'rank'
  | 'relationship'
  | 'assignment'
  | 'outreach'
  | 'equity'
  | 'rate'
  | 'score'
  | 'confidence';

export type SortDir = 'asc' | 'desc';

export interface LeadVirtualRange {
  start: number;
  end: number;
  top: number;
  bottom: number;
}

