/**
 * Which rows the bulk approve gate samples, and whether its samples cover
 * every offer in the run (audit flow-03 / states-06, D-approval-flow-a1).
 *
 * Kept tiny and free of React: the gate (lazy chunk) draws the samples, and
 * useLeadApprovalActions (the LeadTable chunk) re-checks the coverage when a
 * run starts, so this module rides both chunks.
 */
import type { OutreachDraftResult } from '../../lib/apiTypes';

/** The fields the sampler reads from a row. */
export interface CoverageRow {
  borrower_id: string;
  recommended_offer_code?: string | null;
}

/** A row's offer key: its recommended offer code, '' when it has none. */
export function offerKeyOf(row: CoverageRow): string {
  return row.recommended_offer_code ?? '';
}

/**
 * The rows to preview for a run, in table order: the first row of each
 * offer, then top-ups from the offer with the most rows not yet sampled
 * (ties go to the offer seen first) until there are max(offers, min(target,
 * rows)) samples. So every offer is shown at least once, and a run of one
 * offer still shows `target` drafts.
 */
export function stratifiedSampleIds(rows: readonly CoverageRow[], target = 3): string[] {
  const groups = new Map<string, string[]>();
  for (const row of rows) {
    const key = offerKeyOf(row);
    const group = groups.get(key);
    if (group) group.push(row.borrower_id);
    else groups.set(key, [row.borrower_id]);
  }
  const goal = Math.max(groups.size, Math.min(target, rows.length));
  const taken = new Map<string, number>();
  const chosen = new Set<string>();
  for (const [key, ids] of groups) {
    chosen.add(ids[0]);
    taken.set(key, 1);
  }
  while (chosen.size < goal) {
    let bestKey: string | null = null;
    let bestLeft = 0;
    for (const [key, ids] of groups) {
      const left = ids.length - (taken.get(key) ?? 0);
      if (left > bestLeft) {
        bestKey = key;
        bestLeft = left;
      }
    }
    if (bestKey === null) break;
    const next = taken.get(bestKey) ?? 0;
    chosen.add(groups.get(bestKey)![next]);
    taken.set(bestKey, next + 1);
  }
  return rows.map((row) => row.borrower_id).filter((id) => chosen.has(id));
}

export interface BulkSampleCoverage {
  /** Every offer in the run has a ready sample. */
  complete: boolean;
  /** Offers with no ready sample, in table order ('' = no recommended offer). */
  missingOfferCodes: string[];
  /** The first row, in table order, of each missing offer. */
  missingSampleIds: string[];
}

/** Does each offer in `rows` have a ready sample draft among `drafts`? */
export function bulkSampleCoverage(
  rows: readonly CoverageRow[],
  drafts: ReadonlyMap<string, OutreachDraftResult>,
): BulkSampleCoverage {
  const covered = new Set<string>();
  for (const row of rows) {
    if (drafts.has(row.borrower_id)) covered.add(offerKeyOf(row));
  }
  const missingOfferCodes: string[] = [];
  const missingSampleIds: string[] = [];
  for (const row of rows) {
    const key = offerKeyOf(row);
    if (covered.has(key) || missingOfferCodes.includes(key)) continue;
    missingOfferCodes.push(key);
    missingSampleIds.push(row.borrower_id);
  }
  return { complete: missingOfferCodes.length === 0, missingOfferCodes, missingSampleIds };
}

/** The offers a run's previewed drafts showed (a draft's own offer, else its row's). */
export function coveredOfferCodes(
  rows: readonly CoverageRow[],
  drafts: ReadonlyMap<string, OutreachDraftResult>,
): Set<string> {
  const codes = new Set<string>();
  for (const row of rows) {
    const draft = drafts.get(row.borrower_id);
    if (draft) codes.add(draft.offer_code ?? offerKeyOf(row));
  }
  return codes;
}
