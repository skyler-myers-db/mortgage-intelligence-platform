/**
 * Segment-card fixtures for the segments-cards lane (audit 2026-09-21
 * visual-04). Registered per test with `registerVariedSegments(mockApi)`,
 * never in the default registry: they REPLACE `GET /api/segments`.
 *
 * The default six cards render identical content shapes, so a row grid that
 * was not shared across cards would still look aligned. This payload varies
 * exactly the content a real footprint varies, one difference per card:
 *   - `listed` has no contactable gap, so it renders no reconcile note;
 *   - `permit` is on its first snapshot, so its meta row wraps;
 *   - `investor` has no origination-channel mix, so one facet renders.
 * With the subgrid every card still puts each row at the same y.
 */
import type { SegmentSummary } from '../../../../src/types';
import { json, type MockApi } from '../mockApi';
import { SEGMENTS } from './segments';

function varied(row: SegmentSummary): SegmentSummary {
  switch (row.code) {
    case 'listed':
      return { ...row, contactable: row.count };
    case 'permit':
      return { ...row, delta: '+0%' };
    case 'investor':
      return { ...row, origination_channel_mix: [] };
    default:
      return row;
  }
}

export const VARIED_SEGMENTS: readonly SegmentSummary[] = SEGMENTS.map(varied);

export function registerVariedSegments(mockApi: MockApi): void {
  mockApi.register('GET', '/api/segments', () => json<SegmentSummary[]>([...VARIED_SEGMENTS]));
}

/**
 * Six- to eight-digit headline counts (review round 1 of visual-04). A
 * national footprint puts segment counts in the hundreds of thousands and
 * beyond; the count must stay one unbroken line beside `avg`, never
 * `1,234,56` / `7`. Each card keeps a contactable gap, so the reconcile note
 * renders too.
 */
export const BIG_SEGMENT_COUNTS = [123_456, 1_234_567, 12_345_678, 100_000, 999_999, 12_345_678] as const;

export function registerBigCountSegments(mockApi: MockApi): void {
  const rows = SEGMENTS.map((row, index) => {
    const count = BIG_SEGMENT_COUNTS[index % BIG_SEGMENT_COUNTS.length];
    return { ...row, count, contactable: Math.round(count / 9) };
  });
  mockApi.register('GET', '/api/segments', () => json<SegmentSummary[]>(rows));
}
