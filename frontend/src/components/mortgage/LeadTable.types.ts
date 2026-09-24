import type { LeadSummary, SalesTeamMember } from '../../types';
import type { GrowthAgentCohortVerification } from '../../lib/api';
import type { LeadTableView } from './LeadTable.columns';

export interface LeadExportContext {
  generatedAt?: string;
  filters?: string;
  refreshedAt?: string | null;
  rulesVersion?: string | null;
  /**
   * Resolve the offer-rules version on the Export click (audit delivery-08:
   * never on mount). Awaited before the CSV is built and hashed, bounded to
   * 4 s; any failure or timeout stamps `rules_version=unknown` and the
   * export proceeds. Absent for an actor who may not read admin rules.
   * The signal aborts at the bound; an audit-free read may ignore it.
   */
  resolveRulesVersion?: (signal: AbortSignal) => Promise<string | null>;
  /**
   * Non-null while the rows on screen are not the rows of the current
   * filters (a keepPreviousData placeholder): the export would pair the new
   * filters with the previous cohort's ids, so it waits.
   */
  exportBlockedReason?: string | null;
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
