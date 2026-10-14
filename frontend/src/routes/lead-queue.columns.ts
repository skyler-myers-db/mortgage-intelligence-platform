/**
 * The Lead Queue's EFFECTIVE column state (tables-05 step 2, W5e items 2a
 * and 1d): the column preset (`?view=`), the hidden columns (`?hide=`) and
 * the actor's default saved view.
 *
 * Interpretation (12.4): the default saved view governs the COLUMNS only.
 *   - The URL carries `view` or `hide`: the URL governs both (view absent =
 *     Review; hide absent or `none` = nothing hidden).
 *   - Otherwise the actor's default saved view's `view` and `hide` apply.
 *   - Otherwise Review with nothing hidden.
 * The default never injects filters (a drill-in link reads exactly the
 * population it names, D-audit-reads-a), never writes the URL and never
 * reads anything: it is the actor's PRIVATE_LOCAL preference
 * (lead-queue.savedViewPrefs.ts). Columns are display state: none of this
 * reaches /api/leads, the leads or paged query keys, the export filters or
 * the Growth Agent proof key, and a change reads nothing.
 */
import type { LeadTableHiddenColumns, LeadTableHideToken, LeadTableView } from '../components/mortgage/LeadTable.columns';
import {
  LEAD_TABLE_HIDE_NONE,
  LEAD_TABLE_HIDE_PARAM,
  LEAD_TABLE_VIEW_PARAM,
  leadTableHideParamValue,
  parseLeadTableHide,
  parseLeadTableView,
  type LeadQueueColumns,
} from './lead-queue.filters';
import { useSavedViewPrefs, type SavedViewRef } from './lead-queue.savedViewPrefs';

export interface LeadQueueColumnState {
  view: LeadTableView;
  /** The hidden columns of `view`: one shared Set per distinct value (a stable context value). */
  hidden: LeadTableHiddenColumns;
  /** The URL names the columns itself (`view` or `hide` present). */
  explicit: boolean;
  /** The actor has a default saved view. */
  hasDefault: boolean;
}

const hiddenSets = new Map<string, LeadTableHiddenColumns>();

/** The hidden set for `view`: its hideable tokens in `tokens`, interned by value. */
function hiddenFor(view: LeadTableView, tokens: readonly LeadTableHideToken[]): LeadTableHiddenColumns {
  const value = leadTableHideParamValue(view, tokens) ?? '';
  let set = hiddenSets.get(value);
  if (!set) {
    set = new Set(value ? (value.split(',') as LeadTableHideToken[]) : []);
    hiddenSets.set(value, set);
  }
  return set;
}

function columnsOf(params: URLSearchParams): { view: LeadTableView; tokens: LeadTableHideToken[] } {
  const view = parseLeadTableView(params.get(LEAD_TABLE_VIEW_PARAM));
  return { view, tokens: parseLeadTableHide(params.get(LEAD_TABLE_HIDE_PARAM)) };
}

/** Pure: the effective columns of `searchParams` under the actor's default view (or none). */
export function leadQueueColumnState(searchParams: URLSearchParams, defaultView: SavedViewRef | null): LeadQueueColumnState {
  const explicit = searchParams.has(LEAD_TABLE_VIEW_PARAM) || searchParams.has(LEAD_TABLE_HIDE_PARAM);
  const source = explicit ? searchParams : defaultView ? new URLSearchParams(defaultView.params) : null;
  const { view, tokens } = source ? columnsOf(source) : { view: 'default' as const, tokens: [] };
  return { view, hidden: hiddenFor(view, tokens), explicit, hasDefault: defaultView !== null };
}

/** The effective columns for the signed-in actor (reads their saved-view preferences; no request). */
export function useLeadQueueColumnState(searchParams: URLSearchParams): LeadQueueColumnState {
  return leadQueueColumnState(searchParams, useSavedViewPrefs().defaultView);
}

/** The state as share / saved-view input (lead-queue.filters leadQueueShareParams). */
export function leadQueueColumns(state: LeadQueueColumnState): LeadQueueColumns {
  return { view: state.view, hidden: [...state.hidden] };
}

/**
 * Write a column choice into a copy of `searchParams` (every other param is
 * kept): `view` for Sales ops only, `hide` as the view's tokens in column
 * order. With a default saved view, choosing Review with nothing hidden
 * writes `hide=none`, so the explicit choice wins over the default.
 */
export function searchParamsWithLeadTableColumns(
  searchParams: URLSearchParams,
  next: LeadQueueColumns,
  { hasDefault }: { hasDefault: boolean },
): URLSearchParams {
  const out = new URLSearchParams(searchParams);
  if (next.view === 'default') out.delete(LEAD_TABLE_VIEW_PARAM);
  else out.set(LEAD_TABLE_VIEW_PARAM, next.view);
  const hide = leadTableHideParamValue(next.view, next.hidden);
  if (hide !== null) out.set(LEAD_TABLE_HIDE_PARAM, hide);
  else if (hasDefault && next.view === 'default') out.set(LEAD_TABLE_HIDE_PARAM, LEAD_TABLE_HIDE_NONE);
  else out.delete(LEAD_TABLE_HIDE_PARAM);
  return out;
}
