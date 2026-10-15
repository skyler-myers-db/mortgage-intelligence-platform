import { createContext, useContext, type ReactNode } from 'react';
import type { SortKey } from './LeadTable.types';
import './LeadTable.columns.css';

/**
 * Typed column model for the ranked-borrower table (audit tables-05 stage 1,
 * visual-01 / flow-01 / tables-01). One definition drives the colgroup, the
 * header row and the cell order, so a preset can never render a header over
 * the wrong cell.
 *
 * The Review view (URL value: absent, or 'default') is the prototype's column order
 * (design_files/Module 0 Prototype.html:2013-2023: Borrower, Location,
 * Segments, Equity, Rate, Offer, Score, Signal/Confidence, Approval) plus ONE
 * app-added Status cell that merges the four workflow columns the app used to
 * render side by side (Relationship, Assigned to, Outreach, Last touch:
 * ~444px). At 1440x900 with the Console closed that is what lets Score,
 * Signal and Approve sit inside the ~1,318px scrollport. The Sales ops view
 * restores the four workflow columns as separate cells and accepts
 * horizontal scroll behind the sticky Approval pin.
 *
 * Column visibility (tables-05 step 2, W5e; deviation:lead-table-hide-columns):
 * a view's hideable columns can be hidden through `?hide=` (the route's HIDE
 * pill). The selection, row expand, Borrower, Primary offer (it carries the
 * rules evidence chip), Score, Signal and Approval columns are never
 * hideable: evidence, confidence and the approval gate always render. One
 * hidden set (LeadTableHiddenColumnsContext) feeds the colgroup, the header
 * and every row, so a header can never sit over the wrong cell. Segment
 * Intelligence provides no set and keeps the full view.
 */
export type LeadTableView = 'default' | 'sales-ops';

export const LEAD_TABLE_VIEWS: readonly LeadTableView[] = ['default', 'sales-ops'];

export const LEAD_TABLE_VIEW_LABELS: Record<LeadTableView, string> = {
  default: 'Review',
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

/**
 * The `?hide=` vocabulary, in column-model order: URL token -> column key.
 * backend/services/saved_view_params.py mirrors it (parity pinned by
 * tests/unit/test_saved_view_hide_tokens_parity.py, which parses THIS
 * literal). Only these columns are ever hideable.
 */
export const LEAD_TABLE_HIDE_VOCABULARY = [
  { token: 'location', key: 'location' },
  { token: 'relationship', key: 'relationship' },
  { token: 'assigned', key: 'assignment' },
  { token: 'outreach', key: 'outreach' },
  { token: 'last-touch', key: 'lastTouch' },
  { token: 'segments', key: 'segments' },
  { token: 'equity', key: 'equity' },
  { token: 'rate', key: 'rate' },
  { token: 'status', key: 'status' },
] as const satisfies ReadonlyArray<{ token: string; key: LeadTableColumnKey }>;

export type LeadTableHideToken = (typeof LEAD_TABLE_HIDE_VOCABULARY)[number]['token'];
export type LeadTableHiddenColumns = ReadonlySet<LeadTableHideToken>;

const TOKEN_OF_KEY: ReadonlyMap<LeadTableColumnKey, LeadTableHideToken> = new Map(
  LEAD_TABLE_HIDE_VOCABULARY.map(({ token, key }) => [key, token]),
);

/** The column's `?hide=` token, or null when the column is never hideable. */
export function leadTableHideToken(key: LeadTableColumnKey): LeadTableHideToken | null {
  return TOKEN_OF_KEY.get(key) ?? null;
}

/** The view's hideable columns, in column order. */
export function leadTableHideableColumns(view: LeadTableView): readonly LeadTableColumn[] {
  return LEAD_TABLE_COLUMNS[view].filter((column) => TOKEN_OF_KEY.has(column.key));
}

/** No hidden column: the default for every table outside the Lead Queue. */
export const NO_HIDDEN_COLUMNS: LeadTableHiddenColumns = Object.freeze(new Set<LeadTableHideToken>());

// One visible-column array per (hidden set, view), so every row and the
// header share an identity (a compiled row's memo hits across renders).
const visibleCache = new WeakMap<LeadTableHiddenColumns, Partial<Record<LeadTableView, readonly LeadTableColumn[]>>>();

/** The view's visible columns: every column whose hide token is not in `hidden`. */
export function leadTableColumns(view: LeadTableView, hidden: LeadTableHiddenColumns = NO_HIDDEN_COLUMNS): readonly LeadTableColumn[] {
  if (hidden.size === 0) return LEAD_TABLE_COLUMNS[view];
  const cached = visibleCache.get(hidden) ?? {};
  const existing = cached[view];
  if (existing) return existing;
  const visible = LEAD_TABLE_COLUMNS[view].filter((column) => {
    const token = TOKEN_OF_KEY.get(column.key);
    return token === undefined || !hidden.has(token);
  });
  visibleCache.set(hidden, { ...cached, [view]: visible });
  return visible;
}

/** The hidden column set the Lead Queue provides (`?hide=`); empty everywhere else. */
export const LeadTableHiddenColumnsContext = createContext<LeadTableHiddenColumns>(NO_HIDDEN_COLUMNS);

/** The route's HIDE control, rendered by the header after the VIEW pill (keeps its code in the route chunk). */
export const LeadTableHeaderSlotContext = createContext<ReactNode | null>(null);

/** The visible columns of `view` under the provided hidden set. */
export function useLeadTableColumns(view: LeadTableView): readonly LeadTableColumn[] {
  return leadTableColumns(view, useContext(LeadTableHiddenColumnsContext));
}

/** The table's class: the view, plus one `--hide-<token>` per hidden column (LeadTable.columns.css). */
export function leadTableClassName(view: LeadTableView, columns: readonly LeadTableColumn[]): string {
  const visible = new Set(columns.map((column) => column.key));
  const hidden = leadTableHideableColumns(view)
    .filter((column) => !visible.has(column.key))
    .map((column) => `lead-table__table--hide-${TOKEN_OF_KEY.get(column.key)}`);
  const base = `tbl lead-table__table lead-table__table--${view}`;
  return hidden.length === 0 ? base : `${base} lead-table__table--hiding ${hidden.join(' ')}`;
}

/** The sort column's header label, from the FULL view: a hidden sorted column still names itself. */
export function leadTableSortLabel(view: LeadTableView, sortKey: SortKey): string {
  return LEAD_TABLE_COLUMNS[view].find((column) => column.sortKey === sortKey)?.label ?? sortKey;
}

/** Sort keys the merged Status cell keeps reachable through its header menu. */
export const STATUS_SORT_OPTIONS: ReadonlyArray<{ key: SortKey; label: string }> = [
  { key: 'relationship', label: 'Relationship' },
  { key: 'assignment', label: 'Assigned to' },
  { key: 'outreach', label: 'Outreach' },
];
