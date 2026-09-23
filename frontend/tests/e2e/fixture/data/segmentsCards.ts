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
