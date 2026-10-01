/**
 * Signal stack client (`GET /api/segments/combinations`, audit wow-stage-5).
 *
 * Deliberately NOT spread into `api` (lib/api.ts reaches the initial closure):
 * only Segment Intelligence's SignalStack imports it, so it ships in that
 * route's lazy chunk. The read is a whole-book aggregate over gold plus a
 * live contactable count and writes no audit row; it is never prefetched.
 */
import type { SegmentCombinationResponse } from '../../types/segmentCombinations';
import { getJson } from '../apiTransport';

export const SEGMENT_COMBINATIONS_PATH = '/api/segments/combinations';

export const segmentCombinationsApi = {
  combinations: (signal?: AbortSignal) => getJson<SegmentCombinationResponse>(SEGMENT_COMBINATIONS_PATH, signal),
};
