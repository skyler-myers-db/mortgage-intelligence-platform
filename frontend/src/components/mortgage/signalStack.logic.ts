/**
 * Signal stack read model (audit wow-stage-5): pure functions over the exact
 * core-segment combinations `GET /api/segments/combinations` returns.
 *
 * Each row counts the borrowers carrying EXACTLY its set of the six core
 * codes, so the borrowers carrying AT LEAST a set S are the sum of the rows
 * over every superset of S: exact, never a lower bound, because the
 * vocabulary is the closed core (the overlay codes are not in it). A
 * contactable total is null as soon as one contributing row does not report
 * it, never a partial sum.
 */
import type { DrawerSource } from '../AppContext';
import type { SegmentCode } from '../../types';
import type { SegmentCombination, SegmentCombinationProvenance } from '../../types/segmentCombinations';
import { safeSegmentName } from '../../lib/segmentMetadata';

/** The six core codes, in the prototype's seg-grid order. */
export const SIGNAL_STACK_CORE: readonly SegmentCode[] = ['itm', 'listed', 'permit', 'investor', 'equity', 'retention'];
/** The UpSet strip draws at most this many combinations. */
export const UPSET_MAX_COLUMNS = 10;

export interface SignalCount {
  addressable: number;
  /** Null when any contributing row does not report contactable. */
  contactable: number | null;
}

function sum(rows: readonly SegmentCombination[]): SignalCount {
  let addressable = 0;
  let contactable: number | null = 0;
  for (const row of rows) {
    addressable += row.addressable;
    contactable = contactable === null || typeof row.contactable !== 'number' ? null : contactable + row.contactable;
  }
  return { addressable, contactable };
}

/** Borrowers carrying AT LEAST `codes`: the exact rows over every superset. */
export function inclusive(rows: readonly SegmentCombination[], codes: readonly SegmentCode[]): SignalCount {
  return sum(rows.filter((row) => codes.every((code) => row.segment_codes.includes(code))));
}

/** Borrowers firing three or more core signals at once. */
export function threePlus(rows: readonly SegmentCombination[]): SignalCount {
  return sum(rows.filter((row) => row.signal_count >= 3));
}

export interface SignalTriple extends SignalCount {
  codes: SegmentCode[];
}

/** The largest inclusive count over the 20 core triples; ties go to the first in core order. Null when none is populated. */
export function largestTriple(rows: readonly SegmentCombination[]): SignalTriple | null {
  let best: SignalTriple | null = null;
  const core = SIGNAL_STACK_CORE;
  for (let i = 0; i < core.length; i += 1) {
    for (let j = i + 1; j < core.length; j += 1) {
      for (let k = j + 1; k < core.length; k += 1) {
        const codes = [core[i], core[j], core[k]];
        const count = inclusive(rows, codes);
        if (count.addressable > (best?.addressable ?? 0)) best = { codes, ...count };
      }
    }
  }
  return best;
}

/** The UpSet strip's columns: exact rows of two or more signals, largest first, at most ten. */
export function upsetColumns(rows: readonly SegmentCombination[]): SegmentCombination[] {
  return rows
    .filter((row) => row.signal_count >= 2)
    .sort((a, b) => b.addressable - a.addressable)
    .slice(0, UPSET_MAX_COLUMNS);
}

/** The Lead Queue for borrowers carrying every one of `codes` (its segment_mode=all intersection). */
export function leadQueueHref(codes: readonly SegmentCode[]): string {
  const ordered = SIGNAL_STACK_CORE.filter((code) => codes.includes(code));
  const params = new URLSearchParams({ segment_codes: ordered.join(','), segment_mode: 'all' });
  return `/lead-queue?${params.toString()}`;
}

/** "A, B and C" from segment names (an unknown code reads as itself). */
export function signalNames(codes: readonly SegmentCode[]): string {
  const names = codes.map((code) => safeSegmentName(code) ?? code);
  return names.length <= 1 ? names.join('') : `${names.slice(0, -1).join(', ')} and ${names[names.length - 1]}`;
}

/** The evidence drawer source for the stack: the gold rows, the membership they come from, the live subset. */
export function signalStackEvidenceSource(provenance: SegmentCombinationProvenance | null): DrawerSource {
  return {
    title: 'Signal stack: borrowers per exact set of core signals',
    short: 'Signal combinations',
    assetKey: 'segment_combination_rollup',
    assetPath: 'mip.gold.segment_combination_rollup',
    lineageFamily: 'segment_population',
    description:
      'One row per exact set of the six core segments a borrower carries, so every "at least these signals" count is an exact sum of rows. Rebuilt by the gold refresh; contactable counts are live. Whole book, not narrowed by the page filters.',
    signals: [
      { label: 'Exact combinations', source: 'mip.gold.segment_combination_rollup', value: 'one row per exact core set' },
      { label: 'Membership', source: 'mip.gold.borrower_360', value: 'segment_codes, six core codes' },
      { label: 'Contactable', source: 'eligibility predicate', value: 'live, per request' },
      { label: 'Refreshed', source: 'mip.ref.refresh_run_state', value: provenance?.refreshed_at ?? 'current refresh' },
    ],
    updatedAt: provenance?.refreshed_at ?? undefined,
  };
}
