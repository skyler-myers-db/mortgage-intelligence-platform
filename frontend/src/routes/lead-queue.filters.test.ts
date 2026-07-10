import { describe, expect, it } from 'vitest';
import {
  approvalFilterDisplayValue,
  funnelStageDisplayValue,
  isNoOpPortfolioValue,
  LOAN_PRODUCT_FILTER_OPTIONS,
  ORIGINATION_CHANNEL_FILTER_OPTIONS,
  outreachFilterDisplayValue,
  parsePortfolioCriteria,
  parseSegmentCodes,
  portfolioFilterEntries,
  segmentDisplayLabel,
  segmentFilterDisplayValue,
} from './lead-queue.filters';

describe('lead queue effective workflow filters', () => {
  it('shows Approved when the approved funnel stage is driving the filter', () => {
    expect(approvalFilterDisplayValue('any', 'approved')).toBe('Approved');
  });

  it('shows explicit approval status before funnel-derived status', () => {
    expect(approvalFilterDisplayValue('rejected', 'approved')).toBe('Rejected');
  });

  it('shows Actioned when the actioned funnel stage is driving the filter', () => {
    expect(outreachFilterDisplayValue('any', 'actioned')).toBe('Actioned');
  });

  it('falls back to Any labels when no workflow filter is active', () => {
    expect(approvalFilterDisplayValue('any')).toBe('Any approval');
    expect(outreachFilterDisplayValue('any')).toBe('Any outreach');
  });
});

describe('lead queue drilldown display labels', () => {
  it('shows every analytics funnel stage as a user-facing filter value', () => {
    expect(funnelStageDisplayValue('addressable')).toBe('Addressable');
    expect(funnelStageDisplayValue('in_the_money')).toBe('Refi economics');
    expect(funnelStageDisplayValue('high_opportunity')).toBe('Opportunity score 75+');
    expect(funnelStageDisplayValue('offer_recommended')).toBe('Primary offer selected');
    expect(funnelStageDisplayValue('approved')).toBe('Approved');
    expect(funnelStageDisplayValue('actioned')).toBe('Actioned');
  });

  it('maps segment drilldown codes to visible filter names', () => {
    expect(segmentDisplayLabel('listed')).toBe('Listed for Sale');
    expect(segmentDisplayLabel('permit')).toBe('HELOC Intent');
    expect(segmentFilterDisplayValue('listed')).toBe('Listed for Sale');
    expect(segmentFilterDisplayValue('permit')).toBe('HELOC Intent');
    expect(segmentFilterDisplayValue(undefined, ['itm', 'equity'])).toBe('2 segments selected (any selected)');
    expect(segmentFilterDisplayValue(undefined, ['itm', 'equity'], 'all')).toBe('2 segments selected (all selected)');
    expect(segmentDisplayLabel('retention-risk')).toBe('Unknown segment');
    expect(segmentFilterDisplayValue()).toBe('All segments');
  });

  it('normalizes mixed-case segment URL codes', () => {
    expect(parseSegmentCodes('ITM')).toEqual(['itm']);
    expect(parseSegmentCodes('ITM,Equity,itm,drop_table')).toEqual(['itm', 'equity']);
  });
});

describe('S1.6 loan-product and origination-channel filters', () => {
  it('exposes the reviewed loan-product options with the no-op first', () => {
    expect(LOAN_PRODUCT_FILTER_OPTIONS[0]).toBe('All loan products');
    expect([...LOAN_PRODUCT_FILTER_OPTIONS]).toEqual([
      'All loan products',
      'Conventional',
      'Jumbo',
      'FHA',
      'VA',
      'Other',
      'Unknown',
    ]);
  });

  it('exposes the reviewed origination-channel options with the no-op first', () => {
    expect(ORIGINATION_CHANNEL_FILTER_OPTIONS[0]).toBe('All channels');
    expect([...ORIGINATION_CHANNEL_FILTER_OPTIONS]).toEqual([
      'All channels',
      'Loan officer',
      'Digital',
      'Branch',
      'Call center',
      'Unknown',
    ]);
  });

  it('treats the "All ..." sentinels as no-op portfolio values', () => {
    expect(isNoOpPortfolioValue('loan_product', 'All loan products')).toBe(true);
    expect(isNoOpPortfolioValue('origination_channel', 'All channels')).toBe(true);
    expect(isNoOpPortfolioValue('loan_product', 'FHA')).toBe(false);
    expect(isNoOpPortfolioValue('origination_channel', 'Loan officer')).toBe(false);
  });

  it('registers both keys so parse + label round-trips a reviewed value', () => {
    const sp = new URLSearchParams({ loan_product: 'Jumbo', origination_channel: 'Digital' });
    const criteria = parsePortfolioCriteria(sp, []);
    expect(criteria).toMatchObject({ loan_product: 'Jumbo', origination_channel: 'Digital' });
    const entries = portfolioFilterEntries(criteria);
    expect(entries).toEqual(
      expect.arrayContaining([
        { key: 'loan_product', label: 'Product type', value: 'Jumbo' },
        { key: 'origination_channel', label: 'Origination channel', value: 'Digital' },
      ]),
    );
  });

  it('rejects unreviewed loan-product and channel values', () => {
    const sp = new URLSearchParams({ loan_product: 'Reverse', origination_channel: 'Carrier pigeon' });
    expect(parsePortfolioCriteria(sp, [])).toBeUndefined();
  });

  it('accepts backend lowercase/snake_case aliases in deep links', () => {
    const sp = new URLSearchParams({ loan_product: 'jumbo', origination_channel: 'loan_officer' });
    expect(parsePortfolioCriteria(sp, [])).toMatchObject({
      loan_product: 'Jumbo',
      origination_channel: 'Loan officer',
    });
    expect(parsePortfolioCriteria(new URLSearchParams({ loan_product: 'fha' }), []))
      .toMatchObject({ loan_product: 'FHA' });
    expect(parsePortfolioCriteria(new URLSearchParams({ origination_channel: 'call_center' }), []))
      .toMatchObject({ origination_channel: 'Call center' });
  });

  it('treats aliased "all" sentinels and unknown-token aliases as no-ops or rejects', () => {
    // Aliased no-op sentinels resolve to the display sentinel and drop out.
    expect(parsePortfolioCriteria(new URLSearchParams({ loan_product: 'all_loan_products' }), [])).toBeUndefined();
    expect(parsePortfolioCriteria(new URLSearchParams({ origination_channel: 'all_channels' }), [])).toBeUndefined();
    // Unreviewed snake_case tokens are still rejected, not passed through.
    expect(parsePortfolioCriteria(new URLSearchParams({ origination_channel: 'carrier_pigeon' }), [])).toBeUndefined();
  });
});
