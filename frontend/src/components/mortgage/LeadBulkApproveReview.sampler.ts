/**
 * Which rows the bulk approve gate previews (audit flow-03 / states-06,
 * D-approval-flow-a1). Only the gate (LeadBulkApproveReview, lazy chunk)
 * draws samples, so the sampler rides that chunk, not the LeadTable one.
 */
import { offerKeyOf, type CoverageRow } from './LeadBulkApproveReview.coverage';

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
