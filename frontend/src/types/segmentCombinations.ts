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
import type { ApiResponse } from './api.gen';

export type SegmentCombination = ApiResponse<'SegmentCombination'>;

export type SegmentCombinationProvenance = ApiResponse<'SegmentCombinationProvenance'>;

export type SegmentCombinationResponse = ApiResponse<'SegmentCombinationResponse'>;
