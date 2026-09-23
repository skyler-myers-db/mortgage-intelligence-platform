/**
 * Segment-card fixtures for the segments-cards lane (audit 2026-09-21
 * visual-04). Registered per test with `registerVariedSegments(mockApi)`,
 * never in the default registry: they REPLACE `GET /api/segments`.
 *
 * The default six cards render identical content shapes, so a row grid that
 * was not shared across cards would still look aligned. This payload varies
 * exactly the content a real footprint varies, one difference per card:
 *   - `listed` has no contactable gap, so it renders no reconcile note;
 *   - `permit` is on its first snapshot (`+0%`, what every card reads after
 *     a fresh deploy), so its meta row carries the pending-delta status;
 *   - `investor` has no origination-channel mix, so one facet renders;
 *   - `retention` has no borrowers in the current view, so it shows no avg,
 *     no delta and no facets, and its reconcile slot says why.
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
    case 'retention':
      return { ...row, count: 0, contactable: 0, loan_product_mix: [], origination_channel_mix: [] };
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

/**
 * Twelve cards, gated and connected in the same grid rows (review round 2 of
 * visual-04). The backend gates a segment whose Cotality source is not
 * connected or not licensed (databricks_segment_gates.py) and names that
 * source, so these use its real source names, the longest included. A
 * gated card's meta row used to carry the state chip, the source name and
 * the evidence chip, which wrapped to three lines and stretched its whole
 * grid row, connected neighbours included, past 260px.
 */
function overlay(code: SegmentSummary['code'], base: SegmentSummary): SegmentSummary {
  return { ...base, code, name: code, color: `var(--seg-${base.code})` };
}

function gate(row: SegmentSummary, status: 'not_connected' | 'not_licensed', sourceName: string): SegmentSummary {
  return { ...row, source_status: status, source_name: sourceName };
}

export const GATED_SEGMENTS: readonly SegmentSummary[] = (() => {
  const [itm, listed, permit, investor, equity, retention] = SEGMENTS;
  return [
    itm,
    gate(listed, 'not_connected', 'MLS Listings'),
    gate(permit, 'not_licensed', 'Cotality HELOC Propensity'),
    investor,
    equity,
    retention,
    gate(overlay('second_lien_itm', itm), 'not_connected', 'Voluntary Lien'),
    overlay('heloc_draw_to_payback', equity),
    gate(overlay('home_equity_history', equity), 'not_licensed', 'AVM'),
    overlay('refi_propensity', itm),
    gate(overlay('itm_on_related_property', investor), 'not_connected', 'Owner Link'),
    gate(overlay('payoff_loss_leads', retention), 'not_connected', 'MMA Mortgage Analytics'),
  ];
})();

export function registerGatedSegments(mockApi: MockApi): void {
  mockApi.register('GET', '/api/segments', () => json<SegmentSummary[]>([...GATED_SEGMENTS]));
}

/**
 * Every registered segment code (the six cards plus the seven S1.3
 * overlays: the loading grid renders one skeleton per code), connected and
 * in the default Eligible-only view, where the addressable count is the
 * contactable one, so no card renders a reconcile note.
 */
export const ALL_CODE_ELIGIBLE_SEGMENTS: readonly SegmentSummary[] = [
  ...SEGMENTS,
  overlay('second_lien_itm', SEGMENTS[0]),
  overlay('heloc_draw_to_payback', SEGMENTS[4]),
  overlay('home_equity_history', SEGMENTS[4]),
  overlay('refi_propensity', SEGMENTS[0]),
  overlay('itm_on_related_property', SEGMENTS[3]),
  overlay('payoff_loss_leads', SEGMENTS[5]),
  overlay('permit_activity', SEGMENTS[2]),
].map((row) => ({ ...row, contactable: row.count }));
