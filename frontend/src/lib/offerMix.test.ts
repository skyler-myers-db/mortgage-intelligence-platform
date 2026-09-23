import { describe, expect, it } from 'vitest';
import { offerMixSlices, offerPercentText, type OfferMixRow } from './offerMix';

// Live-shaped: "Monitor for later" is ~97% of the offer decisions.
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
const ACTIONABLE_TOTAL = 41_220 + 18_904 + 7 + 55_871 + 3_311 + 912;

describe('offerMixSlices', () => {
  it('draws only primary offer paths: "Monitor for later" is counted apart, never a slice', () => {
    const mix = offerMixSlices(LIVE_SHAPED);
    expect(mix.slices.map((slice) => slice.code)).toEqual([
      'refi', 'purchase', 'refi_plus_heloc', 'investor', 'retention', 'heloc',
    ]);
    // The bar's 100% is the offers_recommended population (the "Primary
    // offer paths" KPI), not offers_available.
    expect(mix.total).toBe(ACTIONABLE_TOTAL);
    expect(mix.monitorCount).toBe(4_620_000);
    // Actionable offers get real shares, not the 1-2% a nurture-led bar left them.
    expect(mix.slices[0]).toMatchObject({ code: 'refi', percent: 46 });
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
    expect(byCode.nurture).toBeUndefined();
    // A product filter is coarser than an offer code; the link says so.
    expect(byCode.refi.queueFilter).toContain('including refinance + home-equity');
    expect(offerMixSlices([{ offer_code: 'cash_out', borrower_count: 3 }]).slices[0].href).toBe(
      '/lead-queue?product=Cash-out',
    );
  });

  it('is empty, never a fabricated 100%, for an absent, all-zero or monitor-only mix', () => {
    expect(offerMixSlices(undefined)).toEqual({ slices: [], total: 0, monitorCount: 0 });
    expect(offerMixSlices(null)).toEqual({ slices: [], total: 0, monitorCount: 0 });
    expect(offerMixSlices([{ offer_code: 'refi', borrower_count: 0 }])).toEqual({
      slices: [],
      total: 0,
      monitorCount: 0,
    });
    expect(offerMixSlices([{ offer_code: 'nurture', borrower_count: 12 }])).toEqual({
      slices: [],
      total: 0,
      monitorCount: 12,
    });
  });

  it('ignores an offer code outside the reviewed vocabulary instead of drawing it', () => {
    const mix = offerMixSlices([
      { offer_code: 'refi', borrower_count: 3 },
      { offer_code: 'mystery', borrower_count: 9 } as unknown as OfferMixRow,
    ]);
    expect(mix.slices.map((slice) => slice.code)).toEqual(['refi']);
    expect(mix.total).toBe(3);
  });
});

describe('offerPercentText', () => {
  it('prints a real but sub-1% share as "<1%", never "0%"', () => {
    expect(offerPercentText(0)).toBe('<1%');
    expect(offerPercentText(1)).toBe('1%');
    expect(offerPercentText(47)).toBe('47%');
  });
});
