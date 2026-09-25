/**
 * The pure models behind the Analytics histograms (dataviz-06, dataviz-10):
 * the partition by lower edge, the governed summaries, and the honest
 * threshold copy (not built versus no single value).
 */
import { describe, expect, it } from 'vitest';
import { HIGH_OPPORTUNITY_SCORE_LABEL } from '../lib/opportunityScore';
import {
  SCORE_BUCKET_WIDTH,
  SPREAD_BUCKET_BPS,
  formatBinRange,
  histogramModel,
  scoreSummary,
  spreadSummary,
  thresholdNotice,
} from './analytics.chart-model';

const SPREAD = [
  { start: -25, count: 100 },
  { start: 0, count: 400 },
  { start: 25, count: 300 },
  { start: 50, count: 120 },
  { start: 75, count: 60 },
  { start: 100, count: 20 },
];

describe('histogramModel', () => {
  it('orders the bins, marks the past ones by lower edge and finds the first modal bin', () => {
    const model = histogramModel([...SPREAD].reverse(), SPREAD_BUCKET_BPS, 75);
    expect(model.bins.map((bin) => bin.start)).toEqual([-25, 0, 25, 50, 75, 100]);
    expect(model.bins.map((bin) => bin.past)).toEqual([false, false, false, false, true, true]);
    expect(model.bins[0].end).toBe(0);
    expect(model.total).toBe(1000);
    expect(model.past).toBe(80);
    expect(model.modal?.start).toBe(0);
  });

  it('never counts a straddled bin, and has no past count without a threshold', () => {
    expect(histogramModel(SPREAD, SPREAD_BUCKET_BPS, 60).past).toBe(80);
    expect(histogramModel(SPREAD, SPREAD_BUCKET_BPS, null).past).toBeNull();
    const tie = histogramModel([{ start: 0, count: 5 }, { start: 5, count: 5 }], SCORE_BUCKET_WIDTH, null);
    expect(tie.modal?.start).toBe(0);
    expect(histogramModel([], SCORE_BUCKET_WIDTH, 75).modal).toBeNull();
  });
});

describe('formatBinRange', () => {
  it('prints integer bin ranges, with words across a negative range', () => {
    expect(formatBinRange(75, SCORE_BUCKET_WIDTH)).toBe('75–79');
    expect(formatBinRange(100, SPREAD_BUCKET_BPS, 'bps')).toBe('100–124 bps');
    expect(formatBinRange(-100, SPREAD_BUCKET_BPS, 'bps')).toBe('-100 to -76 bps');
  });
});

describe('summaries', () => {
  it('states the score distribution from the governed totals, not the bins', () => {
    const model = histogramModel(
      [{ start: 60, count: 400 }, { start: 70, count: 300 }, { start: 75, count: 200 }, { start: 80, count: 100 }],
      SCORE_BUCKET_WIDTH,
      75,
    );
    expect(scoreSummary(model, { addressable_borrowers: 89_553, high_opportunity_borrowers: 4_120 })).toBe(
      `89,553 borrowers scored; the most common band is 60–64 (400). 4,120 (4.6%) score ${HIGH_OPPORTUNITY_SCORE_LABEL}.`,
    );
  });

  it('counts the spread screen only when it sits on a bin edge', () => {
    const aligned = histogramModel(SPREAD, SPREAD_BUCKET_BPS, 75);
    expect(spreadSummary(aligned, { min_spread_bps: 75, reason: null })).toBe(
      '1,000 borrowers plotted between -100 and 400 bps; most common 0–24 bps. 80 (8.0%) sit at or past the 75 bps refi screen.',
    );
    const straddled = histogramModel(SPREAD, SPREAD_BUCKET_BPS, 60);
    expect(spreadSummary(straddled, { min_spread_bps: 60, reason: null })).toBe(
      '1,000 borrowers plotted between -100 and 400 bps; most common 0–24 bps.',
    );
  });

  it('says the screen is not built only when every row is null, and no single value when rows disagree', () => {
    const model = histogramModel(SPREAD, SPREAD_BUCKET_BPS, null);
    expect(spreadSummary(model, { min_spread_bps: null, reason: 'not_built' })).toContain(
      'The refi-screen threshold is not built in this refresh.',
    );
    const disagreeing = spreadSummary(model, { min_spread_bps: null, reason: 'not_uniform' });
    expect(disagreeing).toContain('No single refi-screen threshold applies to this refresh.');
    expect(disagreeing).not.toContain('not built');
    expect(thresholdNotice({ min_spread_bps: 75, reason: null })).toBeNull();
  });
});
