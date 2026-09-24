import { describe, expect, it } from 'vitest';
import {
  approvalFilterDisplayValue,
  funnelStageDisplayValue,
  isNoOpPortfolioValue,
  LOAN_PRODUCT_FILTER_OPTIONS,
  ORIGINATION_CHANNEL_FILTER_OPTIONS,
  outreachFilterDisplayValue,
  parseLeadTablePlace,
  parseLeadTableView,
  parsePortfolioCriteria,
  parseSegmentCodes,
  portfolioFilterEntries,
  searchParamsWithLeadTablePlace,
  searchParamsWithLeadTableView,
  segmentDisplayLabel,
  segmentFilterDisplayValue,
} from './lead-queue.filters';
import { hasLeadQueueFilters, searchParamsCleared } from './lead-queue.activeFilters';

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

describe('lead table column preset param (audit tables-05)', () => {
  it('parses only the reviewed preset and treats anything else as Default', () => {
    expect(parseLeadTableView('sales-ops')).toBe('sales-ops');
    expect(parseLeadTableView(' Sales-Ops ')).toBe('sales-ops');
    expect(parseLeadTableView(null)).toBe('default');
    expect(parseLeadTableView('default')).toBe('default');
    expect(parseLeadTableView('everything')).toBe('default');
  });

  it('writes Sales ops into the URL, drops Default, and keeps every other param', () => {
    const base = new URLSearchParams('state=IL&owner_link=Single-property+owner');
    const salesOps = searchParamsWithLeadTableView(base, 'sales-ops');
    expect(salesOps.toString()).toBe('state=IL&owner_link=Single-property+owner&view=sales-ops');
    expect(searchParamsWithLeadTableView(salesOps, 'default').toString()).toBe('state=IL&owner_link=Single-property+owner');
    expect(base.has('view')).toBe(false);
  });
});

const ROW = 'B-P5YP9ESW32R7Z';

describe('lead table place params (audit shell-03 / runtime-08 / tables-09)', () => {
  it('parses a column sort with its direction and a masked expanded row', () => {
    expect(parseLeadTablePlace(new URLSearchParams(`sort=equity&dir=asc&row=${ROW}`))).toEqual({
      sort: { key: 'equity', dir: 'asc' },
      row: ROW,
    });
    // Direction defaults to descending and is case-insensitive.
    expect(parseLeadTablePlace(new URLSearchParams('sort=Rate')).sort).toEqual({ key: 'rate', dir: 'desc' });
    expect(parseLeadTablePlace(new URLSearchParams('sort=score&dir=ASC')).sort).toEqual({ key: 'score', dir: 'asc' });
  });

  it('drops rank, unknown sort keys, a direction without a sort and anything but a masked id', () => {
    expect(parseLeadTablePlace(new URLSearchParams('sort=rank&dir=asc'))).toEqual({ sort: null, row: null });
    expect(parseLeadTablePlace(new URLSearchParams('sort=owner_name'))).toEqual({ sort: null, row: null });
    expect(parseLeadTablePlace(new URLSearchParams('dir=asc'))).toEqual({ sort: null, row: null });
    for (const row of ['B-123', 'b-p5yp9esw32r7z', 'lo@summit.example', `${ROW}X`, '../borrowers/B-P5YP9ESW32R7Z']) {
      expect(parseLeadTablePlace(new URLSearchParams({ row })).row, row).toBeNull();
    }
  });

  it('writes sort and dir together, returns to rank by deleting both, and keeps every other param', () => {
    const base = new URLSearchParams('state=IL&view=sales-ops');
    const sorted = searchParamsWithLeadTablePlace(base, { sort: { key: 'equity', dir: 'desc' } });
    expect(sorted.toString()).toBe('state=IL&view=sales-ops&sort=equity&dir=desc');
    const expanded = searchParamsWithLeadTablePlace(sorted, { row: ROW });
    expect(expanded.toString()).toBe(`state=IL&view=sales-ops&sort=equity&dir=desc&row=${ROW}`);
    expect(searchParamsWithLeadTablePlace(expanded, { sort: null }).toString()).toBe(`state=IL&view=sales-ops&row=${ROW}`);
    expect(searchParamsWithLeadTablePlace(expanded, { row: null }).toString()).toBe('state=IL&view=sales-ops&sort=equity&dir=desc');
    // Never a non-masked row.
    expect(searchParamsWithLeadTablePlace(base, { row: 'B-123' }).has('row')).toBe(false);
    expect(base.toString()).toBe('state=IL&view=sales-ops');
  });

  it('is not a filter: Clear all stays off for place-only URLs and keeps the place', () => {
    const placeOnly = new URLSearchParams(`view=sales-ops&sort=equity&dir=asc&row=${ROW}`);
    expect(hasLeadQueueFilters(placeOnly)).toBe(false);
    expect(hasLeadQueueFilters(new URLSearchParams(`sort=equity&state=IL`))).toBe(true);
    const cleared = searchParamsCleared(new URLSearchParams(`state=IL&sort=equity&dir=asc&row=${ROW}&view=sales-ops&approval_status=pending`));
    expect(cleared.toString()).toBe(`sort=equity&dir=asc&row=${ROW}&view=sales-ops`);
  });
});
