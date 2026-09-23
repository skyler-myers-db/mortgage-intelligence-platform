import type { SortKey } from './LeadTable.types';

/**
 * Typed column model for the ranked-borrower table (audit tables-05 stage 1,
 * visual-01 / flow-01 / tables-01). One definition drives the colgroup, the
 * header row and the cell order, so a preset can never render a header over
 * the wrong cell.
 *
 * The Default view is the prototype's column order
 * (design_files/Module 0 Prototype.html:2013-2023: Borrower, Location,
 * Segments, Equity, Rate, Offer, Score, Signal/Confidence, Approval) plus ONE
 * app-added Status cell that merges the four workflow columns the app used to
 * render side by side (Relationship, Assigned to, Outreach, Last touch:
 * ~444px). At 1440x900 with the Console closed that is what lets Score,
 * Signal and Approve sit inside the ~1,318px scrollport. The Sales ops view
 * restores the four workflow columns as separate cells and accepts
 * horizontal scroll behind the sticky Approval pin.
 */
export type LeadTableView = 'default' | 'sales-ops';

export const LEAD_TABLE_VIEWS: readonly LeadTableView[] = ['default', 'sales-ops'];

export const LEAD_TABLE_VIEW_LABELS: Record<LeadTableView, string> = {
  default: 'Default',
  'sales-ops': 'Sales ops',
};

export type LeadTableColumnKey =
  | 'select'
  | 'expand'
  | 'borrower'
  | 'location'
  | 'relationship'
  | 'assignment'
  | 'outreach'
  | 'lastTouch'
  | 'segments'
  | 'equity'
  | 'rate'
  | 'offer'
  | 'score'
  | 'confidence'
  | 'status'
  | 'approval';

export interface LeadTableColumn {
  key: LeadTableColumnKey;
  /** Visible header text (empty for the checkbox and chevron columns). */
  label: string;
  /** `<col>` class that owns the width (design-system/components/03-score-and-table.css). */
  colClass: string;
  /** Present on columns whose header is a sort button. */
  sortKey?: SortKey;
  /** Extra `<th>` class (alignment, the sticky Approval pin). */
  thClass?: string;
}

const COLUMN_DEFS: Record<LeadTableColumnKey, LeadTableColumn> = {
  select: { key: 'select', label: '', colClass: 'lead-table__col-select', thClass: 'tbl-cell--select' },
  expand: { key: 'expand', label: '', colClass: 'lead-table__col-expand', thClass: 'tbl-cell--narrow' },
  borrower: { key: 'borrower', label: 'Borrower', colClass: 'lead-table__col-borrower' },
  location: { key: 'location', label: 'Location', colClass: 'lead-table__col-location' },
  relationship: { key: 'relationship', label: 'Relationship', colClass: 'lead-table__col-relationship', sortKey: 'relationship' },
  assignment: { key: 'assignment', label: 'Assigned to', colClass: 'lead-table__col-assignment', sortKey: 'assignment' },
  outreach: { key: 'outreach', label: 'Outreach', colClass: 'lead-table__col-outreach', sortKey: 'outreach' },
  lastTouch: { key: 'lastTouch', label: 'Last touch', colClass: 'lead-table__col-disposition' },
  segments: { key: 'segments', label: 'Segments', colClass: 'lead-table__col-segments' },
  equity: { key: 'equity', label: 'Equity', colClass: 'lead-table__col-equity', sortKey: 'equity', thClass: 'tbl-cell--right' },
  rate: { key: 'rate', label: 'Rate Δ (bps)', colClass: 'lead-table__col-rate', sortKey: 'rate', thClass: 'tbl-cell--right' },
  offer: { key: 'offer', label: 'Primary offer', colClass: 'lead-table__col-offer' },
  score: { key: 'score', label: 'Score', colClass: 'lead-table__col-score', sortKey: 'score', thClass: 'tbl-cell--right' },
  confidence: { key: 'confidence', label: 'Signal', colClass: 'lead-table__col-confidence', sortKey: 'confidence' },
  status: { key: 'status', label: 'Status', colClass: 'lead-table__col-status' },
  approval: {
    key: 'approval',
    label: 'Approval',
    colClass: 'lead-table__col-approval',
    thClass: 'tbl-cell--approval lead-table__approval-header',
  },
};

const VIEW_COLUMN_KEYS: Record<LeadTableView, readonly LeadTableColumnKey[]> = {
  default: [
    'select', 'expand', 'borrower', 'location', 'segments', 'equity', 'rate',
    'offer', 'score', 'confidence', 'status', 'approval',
  ],
  // The workflow columns sit where they always did (after Location): this is
  // the persona that reads them first. Approval stays last and pinned.
  'sales-ops': [
    'select', 'expand', 'borrower', 'location', 'relationship', 'assignment',
    'outreach', 'lastTouch', 'segments', 'equity', 'rate', 'offer', 'score',
    'confidence', 'approval',
  ],
};

export const LEAD_TABLE_COLUMNS: Record<LeadTableView, readonly LeadTableColumn[]> = {
  default: VIEW_COLUMN_KEYS.default.map((key) => COLUMN_DEFS[key]),
  'sales-ops': VIEW_COLUMN_KEYS['sales-ops'].map((key) => COLUMN_DEFS[key]),
};

export function leadTableColumns(view: LeadTableView): readonly LeadTableColumn[] {
  return LEAD_TABLE_COLUMNS[view];
}

/** colSpan for the expanded preview row and the virtual spacer rows. */
export function leadTableColumnCount(view: LeadTableView): number {
  return LEAD_TABLE_COLUMNS[view].length;
}

/** Sort keys the merged Status cell keeps reachable through its header menu. */
export const STATUS_SORT_OPTIONS: ReadonlyArray<{ key: SortKey; label: string }> = [
  { key: 'relationship', label: 'Relationship' },
  { key: 'assignment', label: 'Assigned to' },
  { key: 'outreach', label: 'Outreach' },
];
