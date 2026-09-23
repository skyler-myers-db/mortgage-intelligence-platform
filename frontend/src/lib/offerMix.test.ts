import { describe, expect, it } from 'vitest';
import { offerMixSlices, type OfferMixRow } from './offerMix';

const LIVE_SHAPED: OfferMixRow[] = [
  { offer_code: 'purchase', borrower_count: 41_220 },
  { offer_code: 'refi_plus_heloc', borrower_count: 18_904 },
  { offer_code: 'heloc', borrower_count: 7 },
  { offer_code: 'refi', borrower_count: 55_871 },
  { offer_code: 'cash_out', borrower_count: 0 },
  { offer_code: 'investor', borrower_count: 3_311 },
  { offer_code: 'retention', borrower_count: 912 },
  { offer_code: 'nurture', borrower_count: 4_620_000 },
];

describe('offerMixSlices', () => {
  it('orders by size, drops zero counts and totals the offer decisions', () => {
    const mix = offerMixSlices(LIVE_SHAPED);
    expect(mix.slices.map((slice) => slice.code)).toEqual([
      'nurture', 'refi', 'purchase', 'refi_plus_heloc', 'investor', 'retention', 'heloc',
    ]);
    expect(mix.total).toBe(4_740_225);
  });

  it('keeps exact shares for the bar and whole percents that always add up to 100', () => {
    const mix = offerMixSlices(LIVE_SHAPED);
    const exact = mix.slices.reduce((sum, slice) => sum + slice.share, 0);
    expect(exact).toBeCloseTo(100, 10);
    expect(mix.slices.reduce((sum, slice) => sum + slice.percent, 0)).toBe(100);
    // Largest remainder: every percent is its floor or its ceiling.
    for (const slice of mix.slices) {
      expect([Math.floor(slice.share), Math.ceil(slice.share)]).toContain(slice.percent);
    }
  });

  it('breaks three-way thirds deterministically', () => {
    const mix = offerMixSlices([
      { offer_code: 'refi', borrower_count: 1 },
      { offer_code: 'heloc', borrower_count: 1 },
      { offer_code: 'purchase', borrower_count: 1 },
    ]);
    expect(mix.slices.map((slice) => [slice.code, slice.percent])).toEqual([
      ['heloc', 34],
      ['purchase', 33],
      ['refi', 33],
    ]);
  });

  it('opens each offer through the Lead Queue product filter, naming what it applies', () => {
    const byCode = Object.fromEntries(offerMixSlices(LIVE_SHAPED).slices.map((slice) => [slice.code, slice]));
    expect(byCode.refi.href).toBe('/lead-queue?product=Refi');
    expect(byCode.refi_plus_heloc.href).toBe('/lead-queue?product=Refi');
    expect(byCode.heloc.href).toBe('/lead-queue?product=HELOC');
    expect(byCode.purchase.href).toBe('/lead-queue?product=Purchase');
    expect(byCode.retention.href).toBe('/lead-queue?product=Retention');
    expect(byCode.investor.href).toBe('/lead-queue?segment=investor');
    // "Monitor for later" recommends no outreach: there is no queue for it.
    expect(byCode.nurture.href).toBeNull();
    expect(byCode.nurture.label).toBe('Monitor for later');
    // A product filter is coarser than an offer code; the link says so.
    expect(byCode.refi.queueFilter).toContain('including refinance + home-equity');
    expect(offerMixSlices([{ offer_code: 'cash_out', borrower_count: 3 }]).slices[0].href).toBe(
      '/lead-queue?product=Cash-out',
    );
  });

  it('is empty, never a fabricated 100%, for an absent or all-zero mix', () => {
    expect(offerMixSlices(undefined)).toEqual({ slices: [], total: 0 });
    expect(offerMixSlices(null)).toEqual({ slices: [], total: 0 });
    expect(offerMixSlices([{ offer_code: 'refi', borrower_count: 0 }])).toEqual({ slices: [], total: 0 });
  });
});
