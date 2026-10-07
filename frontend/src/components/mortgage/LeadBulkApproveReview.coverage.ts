/**
 * Whether the bulk approve gate's samples cover every offer in the run
 * (audit flow-03 / states-06, D-approval-flow-a1).
 *
 * Kept tiny and free of React: useLeadApprovalActions (the LeadTable chunk)
 * re-checks the coverage when a run starts, so this module rides the
 * LeadTable chunk. Which rows are sampled is the gate's alone
 * (LeadBulkApproveReview.sampler, lazy chunk).
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

export interface BulkSampleCoverage {
  /** Every offer in the run has a ready sample. */
  complete: boolean;
  /** Offers with no ready sample, in table order ('' = no recommended offer). */
  missingOfferCodes: string[];
  /** The first row, in table order, of each missing offer. */
  missingSampleIds: string[];
}

/**
 * Does each offer in `rows` appear in a ready sample draft among `drafts`?
 * An offer counts as covered only when a previewed draft really showed it
 * (coveredOfferCodes: the draft's own offer_code, else its row's), never
 * merely because its row has a ready sample (W5a integrator ruling R1).
 * Arming-only: the in-run check and the canary already refuse a mismatch.
 */
export function bulkSampleCoverage(
  rows: readonly CoverageRow[],
  drafts: ReadonlyMap<string, OutreachDraftResult>,
): BulkSampleCoverage {
  const covered = coveredOfferCodes(rows, drafts);
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
