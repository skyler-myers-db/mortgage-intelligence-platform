import type { LeadSummary, SalesTeamMember } from '../../types';
import type { GrowthAgentCohortVerification } from '../../lib/api';
import type { LeadTableView } from './LeadTable.columns';

export interface LeadExportContext {
  generatedAt?: string;
  filters?: string;
  refreshedAt?: string | null;
  rulesVersion?: string | null;
  /** Which rows the file holds: the operator's selection or the loaded list. */
  scope?: 'selected_rows' | 'loaded_rows';
  /** On-screen order the rows were written in, e.g. `rank` or `equity desc`. */
  rowOrder?: string;
}

export interface LeadTableProps {
  leads: LeadSummary[];
  totalMatching?: number | null;
  truncatedAt?: number | null;
  growthAgentVerification?: GrowthAgentCohortVerification | null;
  exportContext?: LeadExportContext;
  salesTeam?: SalesTeamMember[];
  /** Column preset (audit tables-05). Default: the prototype columns + Status. */
  view?: LeadTableView;
  /** Renders the View control; the route persists the choice in `?view=`. */
  onViewChange?: (view: LeadTableView) => void;
  /** Size the scroller to the viewport (floored at 480px) instead of the fixed 520px cap. */
  fillHeight?: boolean;
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
