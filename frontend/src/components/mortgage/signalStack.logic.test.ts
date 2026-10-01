/**
 * Signal stack read model (audit wow-stage-5): inclusive counts are exact sums
 * of exact rows, the headline counts three or more signals, the largest
 * triple breaks ties in core order, a null contactable propagates, and links
 * open the Lead Queue's segment_mode=all intersection.
 */
import { describe, expect, it } from 'vitest';
import type { SegmentCode } from '../../types';
import type { SegmentCombination } from '../../types/segmentCombinations';
import {
  inclusive,
  largestTriple,
  leadQueueHref,
  signalNames,
  signalStackEvidenceSource,
  threePlus,
  upsetColumns,
} from './signalStack.logic';

const row = (codes: SegmentCode[], addressable: number, contactable: number | null = Math.round(addressable / 10)): SegmentCombination => ({
  segment_codes: codes,
  signal_count: codes.length,
  addressable,
  contactable,
});

const ROWS: SegmentCombination[] = [
  row(['itm'], 9000, 900),
  row(['itm', 'equity'], 2000, 200),
  row(['itm', 'permit', 'equity'], 500, 50),
  row(['itm', 'listed', 'equity', 'retention'], 90, 9),
  row(['itm', 'investor', 'retention'], 180, 18),
  row(['listed', 'retention'], 260, 26),
];

describe('signal stack logic', () => {
  it('sums every superset for an inclusive count', () => {
    expect(inclusive(ROWS, ['itm'])).toEqual({ addressable: 11770, contactable: 1177 });
    expect(inclusive(ROWS, ['itm', 'equity'])).toEqual({ addressable: 2590, contactable: 259 });
    expect(inclusive(ROWS, ['retention'])).toEqual({ addressable: 530, contactable: 53 });
    expect(inclusive(ROWS, ['investor', 'listed'])).toEqual({ addressable: 0, contactable: 0 });
  });

  it('counts three or more signals for the headline, never two', () => {
    expect(threePlus(ROWS)).toEqual({ addressable: 770, contactable: 77 });
  });

  it('propagates an unreported contactable as null', () => {
    const rows = [...ROWS.slice(0, 2), row(['itm', 'permit', 'equity'], 500, null)];
    expect(inclusive(rows, ['itm']).contactable).toBeNull();
    expect(inclusive(rows, ['equity', 'itm']).contactable).toBeNull();
    expect(inclusive(rows, ['itm', 'equity']).addressable).toBe(2500);
    expect(threePlus(rows)).toEqual({ addressable: 500, contactable: null });
    // A null row that does not contribute leaves the count reported.
    expect(inclusive(rows, ['listed'])).toEqual({ addressable: 0, contactable: 0 });
  });

  it('picks the largest triple, ties in core order', () => {
    expect(largestTriple(ROWS)).toEqual({ codes: ['itm', 'permit', 'equity'], addressable: 500, contactable: 50 });
    // A tie: (itm, listed, equity) and (itm, equity, retention) both carry 90;
    // core order (itm, listed, permit, investor, equity, retention) keeps the first.
    const tie = [row(['itm', 'listed', 'equity', 'retention'], 90, 9)];
    expect(largestTriple(tie)?.codes).toEqual(['itm', 'listed', 'equity']);
    expect(largestTriple([row(['itm', 'equity'], 10)])).toBeNull();
  });

  it('draws the UpSet columns from exact rows of two or more signals, largest first, at most ten', () => {
    expect(upsetColumns(ROWS).map((r) => r.segment_codes.join('+'))).toEqual([
      'itm+equity',
      'itm+permit+equity',
      'listed+retention',
      'itm+investor+retention',
      'itm+listed+equity+retention',
    ]);
    const many = Array.from({ length: 14 }, (_, index) => row(['itm', 'equity'], 100 + index));
    expect(upsetColumns(many)).toHaveLength(10);
  });

  it('links the inclusive set into the Lead Queue intersection, in core order', () => {
    const href = leadQueueHref(['equity', 'itm', 'permit']);
    const url = new URL(href, 'https://app.example');
    expect(url.pathname).toBe('/lead-queue');
    expect(url.searchParams.get('segment_codes')).toBe('itm,permit,equity');
    expect(url.searchParams.get('segment_mode')).toBe('all');
  });

  it('names the signals and points the evidence at the gold table', () => {
    expect(signalNames(['itm', 'permit', 'equity'])).toBe('Prime Refi Candidates, HELOC Intent and Home Equity Candidate');
    expect(signalNames(['listed'])).toBe('Listed for Sale');
    const source = signalStackEvidenceSource({ source: 's', contactable_source: 'c', refreshed_at: '2026-07-14 12:00:00', note: 'n' });
    expect(source).toMatchObject({
      assetKey: 'segment_combination_rollup',
      assetPath: 'mip.gold.segment_combination_rollup',
      lineageFamily: 'segment_population',
      updatedAt: '2026-07-14 12:00:00',
    });
  });
});
