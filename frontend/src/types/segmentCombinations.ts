/**
 * Signal stack wire types (`GET /api/segments/combinations`, audit
 * wow-stage-5), named exactly after the Pydantic models in
 * backend/schemas/segment_combinations.py.
 *
 * One row per non-empty EXACT set of the six core segment codes a borrower
 * carries; inclusive counts ("at least these") are sums of rows. Addressable
 * is precomputed in mip.gold.segment_combination_rollup; contactable is a
 * live subset (null = not reported, never zero).
 */
import type { SegmentCode } from '../types';

export interface SegmentCombination {
  /** The exact set, distinct and in core order. */
  segment_codes: SegmentCode[];
  signal_count: number;
  addressable: number;
  contactable?: number | null;
}

export interface SegmentCombinationProvenance {
  source: string;
  contactable_source: string;
  refreshed_at?: string | null;
  note: string;
}

export interface SegmentCombinationResponse {
  /** False until the gold refresh has built the table. */
  built: boolean;
  core_codes: SegmentCode[];
  /** Largest addressable first, then by combination key. */
  combinations: SegmentCombination[];
  provenance: SegmentCombinationProvenance;
}
