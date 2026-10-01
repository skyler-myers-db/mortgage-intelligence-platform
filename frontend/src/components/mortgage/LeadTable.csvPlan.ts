/**
 * What the Export button and its strip need while the table renders: the
 * eligibility gate, the plan (which rows, which scope) and the honest
 * partial-scope counts (D-approval-flow-b). The file itself is built by
 * LeadTable.csv, which loads on the Export click, so the shared LeadTable
 * chunk carries none of the CSV columns, metadata lines or the download.
 */
import type { LeadSummary } from '../../types';

/**
 * S1.4 fail-closed export gate: eligible, opt-in, and not do-not-contact.
 * Gold already folds dnc/opt-out into marketing_eligible; the explicit
 * checks are defense-in-depth so a stale row can never leak into a CSV.
 * Exported so the button label and the confirmation count the SAME rows the
 * file will hold (audit tables-08: the label said "500 leads" while the gate
 * wrote zero rows).
 */
export function isLeadCsvExportable(lead: LeadSummary): boolean {
  return (
    lead.marketing_eligible === true &&
    lead.dnc !== true &&
    (lead.consent_status ?? 'opt_in') === 'opt_in'
  );
}

export interface LeadCsvExportPlan {
  /** Post-eligibility rows, in the on-screen (sorted) order. */
  rows: LeadSummary[];
  scope: 'selected_rows' | 'loaded_rows';
  /** Rows in scope that the eligibility gate dropped. */
  excluded: number;
}

/**
 * What an export would write right now (audit tables-08). The selection wins
 * when one exists; otherwise the currently sorted rows — never the unsorted
 * `leads` prop, which ignored both.
 */
export function planLeadCsvExport(
  sortedLeads: LeadSummary[],
  selectedIds: ReadonlySet<string>,
): LeadCsvExportPlan {
  const selected = sortedLeads.filter((lead) => selectedIds.has(lead.borrower_id));
  const inScope = selected.length > 0 ? selected : sortedLeads;
  const rows = inScope.filter(isLeadCsvExportable);
  return {
    rows,
    scope: selected.length > 0 ? 'selected_rows' : 'loaded_rows',
    excluded: inScope.length - rows.length,
  };
}

/**
 * The matching-borrower count an export may state (D-approval-flow-b): known
 * and covering the file, else null (the file says `unknown` and the receipt
 * omits the field, so a stale count can never refuse the receipt).
 */
export function exportMatchingRows(matching: number | null | undefined, rowCount: number): number | null {
  return typeof matching === 'number' && Number.isFinite(matching) && matching >= rowCount ? matching : null;
}

/** M when a loaded-rows export is partial: more borrowers match than are loaded. */
export function loadedExportTruncatedOf(
  scope: LeadCsvExportPlan['scope'],
  matching: number | null | undefined,
  loadedCount: number,
): number | null {
  return scope === 'loaded_rows' && typeof matching === 'number' && matching > loadedCount ? matching : null;
}
