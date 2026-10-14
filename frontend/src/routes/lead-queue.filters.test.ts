import { describe, expect, it } from 'vitest';
import {
  KNOWN_QUEUE_PARAMS,
  LEAD_TABLE_HIDE_PARAM,
  leadTableHideParamValue,
  parseBorrowerIds,
  parseLeadTableHide,
  APPROVAL_REQUEST_BATCH_PARAM,
  LEAD_BOUND_FILTER_KEYS,
  LEAD_BOUND_LIMITS,
  approvalFilterDisplayValue,
  buildLeadQueueExportFilters,
  leadBoundsBlocked,
  parseLeadBound,
  funnelStageDisplayValue,
  isNoOpPortfolioValue,
  LOAN_PRODUCT_FILTER_OPTIONS,
  ORIGINATION_CHANNEL_FILTER_OPTIONS,
  outreachFilterDisplayValue,
  leadQueueShareParams,
  parseApprovalRequestBatch,
  parseLeadTablePlace,
  parseLeadTableView,
  parsePortfolioCriteria,
  parseSegmentCodes,
  parseTriageMode,
  portfolioFilterEntries,
  searchParamsWithLeadTablePlace,
  searchParamsWithLeadTableView,
  searchParamsWithTriageMode,
  searchWithoutTriageMode,
  segmentDisplayLabel,
  segmentFilterDisplayValue,
} from './lead-queue.filters';
import { hasLeadQueueFilters, searchParamsCleared } from './lead-queue.activeFilters';
import { leadsRequestFromSearchParams } from './lead-queue.request';
import { leadsQuery } from '../lib/leadsQuery';
import { MAX_QUEUE_BORROWER_IDS } from '../lib/queueBorrowerIds';

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
    expect(funnelStageDisplayValue('in_the_money')).toBe('In the Money');
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

describe('Copy link share params (audit tables-09)', () => {
  it('keeps the sanitized filters, segment mode, sort, dir, view, campaign binding and assigned_to=me', () => {
    const raw = new URLSearchParams(
      'segment_codes=itm,equity&segment_mode=all&state=IL&approval_status=pending&assigned_to=me'
      + '&sort=equity&dir=asc&view=sales-ops&campaign_id=cmp-1&variant_name=Variant+A',
    );
    const share = leadQueueShareParams(raw, {
      segmentCodes: ['itm', 'equity'],
      segmentMode: 'all',
      stateFilter: 'IL',
      approvalStatus: 'pending',
      assignedTo: 'me',
    });
    const params = new URLSearchParams(share.search);
    expect(Object.fromEntries(params)).toEqual({
      segment_codes: 'itm,equity',
      segment_mode: 'all',
      state: 'IL',
      approval_status: 'pending',
      assigned_to: 'me',
      sort: 'equity',
      dir: 'asc',
      view: 'sales-ops',
      campaign_id: 'cmp-1',
      variant_name: 'Variant A',
    });
    expect(share.omitted).toEqual([]);
  });

  it('drops the open row, an assignee email, the Growth Agent proof and unknown keys, and names them', () => {
    const raw = new URLSearchParams(
      `state=IL&row=${ROW}&assigned_to=lo.one%40summit.example&growth_agent_run_id=11111111-1111-4111-8111-111111111111`
      + '&tool_result_hash=abc&utm_source=mail&debug=1',
    );
    const share = leadQueueShareParams(raw, { stateFilter: 'IL', assignedTo: 'lo.one@summit.example' });
    expect(share.search).toBe('?state=IL');
    expect(share.search).not.toContain('@');
    expect(share.omitted).toEqual([
      'the open row',
      'the assignee email',
      'the Growth Agent proof',
      '2 unrecognized parameters',
    ]);
  });

  it('is an empty query for the bare queue', () => {
    expect(leadQueueShareParams(new URLSearchParams(), {})).toEqual({ search: '', omitted: [] });
  });
});

describe('public score and rate-spread bounds (audit tables-06, wow-stage-1)', () => {
  const bounds = (query: string) => parsePortfolioCriteria(new URLSearchParams(query), []);

  it('parses whole numbers inside each range into the portfolio criteria record', () => {
    expect(LEAD_BOUND_FILTER_KEYS).toEqual([
      'min_opportunity_score', 'max_opportunity_score', 'min_rate_spread_bps', 'max_rate_spread_bps',
    ]);
    expect(LEAD_BOUND_LIMITS).toEqual({ opportunity_score: { min: 0, max: 100 }, rate_spread_bps: { min: -1000, max: 5000 } });
    expect(bounds('min_opportunity_score=70&max_opportunity_score=90&min_rate_spread_bps=-25&occupancy=Owner-occupied')).toEqual({
      occupancy: 'Owner-occupied',
      min_opportunity_score: '70',
      max_opportunity_score: '90',
      min_rate_spread_bps: '-25',
    });
  });

  it('drops out-of-range, fractional and non-numeric values', () => {
    expect(bounds('min_opportunity_score=101')).toBeUndefined();
    expect(bounds('max_rate_spread_bps=-1001')).toBeUndefined();
    expect(bounds('min_opportunity_score=70.5')).toBeUndefined();
    expect(bounds('max_opportunity_score=high')).toBeUndefined();
    expect(bounds('min_opportunity_score=%2070%20')).toEqual({ min_opportunity_score: '70' });
  });

  it('drops an inverted pair entirely and keeps the other dimension', () => {
    expect(bounds('min_opportunity_score=91&max_opportunity_score=90&max_rate_spread_bps=100')).toEqual({
      max_rate_spread_bps: '100',
    });
  });

  it('sends no bound beside a Genie cohort or a Growth Agent proof', () => {
    expect(bounds('min_opportunity_score=70&cohort_id=11111111-1111-1111-1111-111111111111')).toBeUndefined();
    expect(bounds('max_rate_spread_bps=50&growth_handoff=signed')).toBeUndefined();
    expect(bounds('max_rate_spread_bps=50&tool_result_hash=abc')).toBeUndefined();
  });

  it('reaches the export fingerprint and Copy link after the portfolio keys, as known params', () => {
    const criteria = bounds('occupancy=Owner-occupied&max_opportunity_score=90&min_rate_spread_bps=-25');
    expect(buildLeadQueueExportFilters({ stateFilter: 'IL', portfolioCriteria: criteria })).toBe(
      'state=IL&occupancy=Owner-occupied&max_opportunity_score=90&min_rate_spread_bps=-25',
    );
    const raw = new URLSearchParams('state=IL&occupancy=Owner-occupied&max_opportunity_score=90&min_rate_spread_bps=-25');
    const share = leadQueueShareParams(raw, { stateFilter: 'IL', portfolioCriteria: criteria });
    expect(share).toEqual({
      search: '?state=IL&occupancy=Owner-occupied&max_opportunity_score=90&min_rate_spread_bps=-25',
      omitted: [],
    });
  });

  it('never exports a bound beside a cohort, even from a hand-built criteria record', () => {
    expect(buildLeadQueueExportFilters({
      cohortId: '11111111-1111-1111-1111-111111111111',
      portfolioCriteria: { min_opportunity_score: '70' },
    })).toBe('cohort_id=11111111-1111-1111-1111-111111111111');
  });

  it('parseLeadBound and leadBoundsBlocked are the shared primitives', () => {
    expect(parseLeadBound('min_rate_spread_bps', '-1000')).toBe(-1000);
    expect(parseLeadBound('min_rate_spread_bps', '-1001')).toBeUndefined();
    expect(parseLeadBound('max_opportunity_score', '')).toBeUndefined();
    expect(leadBoundsBlocked(new URLSearchParams('cohort_id=%20'))).toBe(false);
    expect(leadBoundsBlocked(new URLSearchParams('growth_agent_run_id=r'))).toBe(true);
  });
});

describe('the Triage deck deep link (D-approval-flow-a2)', () => {
  it('parses only `triage` as a mode', () => {
    expect(parseTriageMode('triage')).toBe('triage');
    expect(parseTriageMode(' TRIAGE ')).toBe('triage');
    expect(parseTriageMode('deck')).toBeNull();
    expect(parseTriageMode(null)).toBeNull();
  });

  it('enters keeping filters, sort and the campaign binding; leaving names the row to return to', () => {
    const base = new URLSearchParams('state=IL&sort=equity&dir=asc&campaign_id=c1&variant_name=A');
    const entered = searchParamsWithTriageMode(base, 'triage');
    expect(Object.fromEntries(entered)).toEqual({
      state: 'IL', sort: 'equity', dir: 'asc', campaign_id: 'c1', variant_name: 'A', mode: 'triage',
    });
    const left = searchParamsWithTriageMode(entered, null, 'B-0000000000001');
    expect(left.get('mode')).toBeNull();
    expect(left.get('row')).toBe('B-0000000000001');
    expect(searchParamsWithTriageMode(entered, null, 'not-an-id').get('row')).toBeNull();
  });

  it('Copy link and saved views omit it, naming it "the triage deck"', () => {
    const share = leadQueueShareParams(new URLSearchParams('state=IL&mode=triage'), { stateFilter: 'IL' });
    expect(share.search).not.toContain('mode');
    expect(share.omitted).toEqual(['the triage deck']);
  });

  it('never reaches the leads request or its query key (no refetch, no VIEW_LEADS row)', () => {
    const refs = ['All'];
    const without = new URLSearchParams('state=IL&segment_codes=itm&sort=equity');
    const withMode = searchParamsWithTriageMode(without, 'triage');
    const key = (sp: URLSearchParams) => JSON.stringify(
      leadsQuery('lead-queue', leadsRequestFromSearchParams(sp, refs, null), ['']).queryKey,
    );
    expect(key(withMode)).toBe(key(without));
  });

  it('is stripped from the search the queue publishes for dossier crumbs', () => {
    expect(searchWithoutTriageMode('?state=IL&mode=triage&row=B-0000000000001')).toBe('?state=IL&row=B-0000000000001');
    expect(searchWithoutTriageMode('?mode=triage')).toBe('');
    expect(searchWithoutTriageMode('?state=IL')).toBe('?state=IL');
  });
});

describe('the approval-request scope param (flow-02 / shell-06, D-approval-flow-c)', () => {
  const ID = '0f8fad5b-d9cb-469f-a165-70867728950e';
  const sp = (query: string) => new URLSearchParams(query);

  it('parses a lower-cased 8-4-4-4-12 hex UUID and nothing else', () => {
    expect(APPROVAL_REQUEST_BATCH_PARAM).toBe('approval_request_batch');
    expect(parseApprovalRequestBatch(sp(`approval_request_batch=${ID}`))).toBe(ID);
    expect(parseApprovalRequestBatch(sp(`approval_request_batch=${ID.toUpperCase()}`))).toBe(ID);
    expect(parseApprovalRequestBatch(sp(`approval_request_batch=%20${ID}%20`))).toBe(ID);
    for (const bad of ['', 'abc', `${ID}0`, ID.replace(/-/g, ''), `{${ID}}`, 'zf8fad5b-d9cb-469f-a165-70867728950e']) {
      expect(parseApprovalRequestBatch(sp(`approval_request_batch=${bad}`))).toBeUndefined();
    }
    expect(parseApprovalRequestBatch(sp('state=IL'))).toBeUndefined();
  });

  it('is unset beside every param the server refuses with it, and beside a campaign binding (v1 is unbound only)', () => {
    const conflicts = [
      'cohort_id=c1', 'borrower_ids=B-0123456789ABC', 'campaign_id=c1', 'variant_name=A',
      'growth_agent_run_id=r', 'actionable_total=3', 'actionable_cohort_fingerprint=f',
      'actionable_snapshot_id=s', 'tool_result_hash=h', 'growth_handoff=g',
    ];
    for (const conflict of conflicts) {
      expect(parseApprovalRequestBatch(sp(`approval_request_batch=${ID}&${conflict}`))).toBeUndefined();
    }
    // An empty conflicting param is no conflict.
    expect(parseApprovalRequestBatch(sp(`approval_request_batch=${ID}&cohort_id=`))).toBe(ID);
    expect(parseApprovalRequestBatch(sp(`approval_request_batch=${ID}&state=IL&sort=equity`))).toBe(ID);
  });

  it('Copy link and saved views omit it as "the approval request" (known, never unrecognized)', () => {
    const share = leadQueueShareParams(sp(`state=IL&approval_request_batch=${ID}`), { stateFilter: 'IL' });
    expect(share.search).toBe('?state=IL');
    expect(share.omitted).toEqual(['the approval request']);
  });

  it('enables Clear all, which drops it with every other filter', () => {
    const scoped = sp(`approval_request_batch=${ID}&view=sales-ops&sort=equity&dir=asc`);
    expect(hasLeadQueueFilters(scoped)).toBe(true);
    expect(searchParamsCleared(scoped).get(APPROVAL_REQUEST_BATCH_PARAM)).toBeNull();
  });

  it('reaches the leads request and its query key only while scoped', () => {
    const refs = ['All'];
    const key = (query: string) => JSON.stringify(
      leadsQuery('lead-queue', leadsRequestFromSearchParams(sp(query), refs, null), ['']).queryKey,
    );
    expect(leadsRequestFromSearchParams(sp(`approval_request_batch=${ID}`), refs, null).opts?.approvalRequestBatch).toBe(ID);
    expect(leadsRequestFromSearchParams(sp('state=IL'), refs, null).opts?.approvalRequestBatch).toBeUndefined();
    expect(key(`approval_request_batch=${ID}`)).not.toBe(key(''));
    expect(key(`approval_request_batch=${ID}`)).toContain(ID);
    expect(key(`approval_request_batch=${ID}&cohort_id=c1`)).toBe(key('cohort_id=c1'));
  });
});

describe('the borrower_ids hand-off cap (W5d follow-ups deferral)', () => {
  const ids = (count: number) => Array.from({ length: count }, (_, index) => `B-${String(index).padStart(13, '0')}`);

  it('keeps a 50-id deep link whole and truncates the 51st id client-side', () => {
    expect(MAX_QUEUE_BORROWER_IDS).toBe(50);
    expect(parseBorrowerIds(ids(50).join(','))).toEqual(ids(50));
    expect(parseBorrowerIds(ids(51).join(','))).toEqual(ids(50));
  });

  it('counts only valid, de-duplicated ids toward the cap', () => {
    const raw = ['not-an-id', ...ids(50), ids(50)[0], 'B-ZZZZZZZZZZZZZ'].join(',');
    expect(parseBorrowerIds(raw)).toEqual(ids(50));
  });
});

describe('the ?hide= grammar (tables-05 step 2)', () => {
  it('reads vocabulary tokens in column order, de-duplicated and lower-cased; none and junk name none', () => {
    expect(LEAD_TABLE_HIDE_PARAM).toBe('hide');
    expect(parseLeadTableHide('rate,SEGMENTS,rate, location ')).toEqual(['location', 'segments', 'rate']);
    expect(parseLeadTableHide('none')).toEqual([]);
    expect(parseLeadTableHide('borrower,score,approval,offer,confidence,zyrplax')).toEqual([]);
    expect(parseLeadTableHide(null)).toEqual([]);
  });

  it("writes only the view's hideable tokens, or nothing", () => {
    expect(leadTableHideParamValue('default', ['rate', 'relationship', 'segments'])).toBe('segments,rate');
    expect(leadTableHideParamValue('sales-ops', ['status', 'assigned'])).toBe('assigned');
    expect(leadTableHideParamValue('default', [])).toBeNull();
  });

  it('is a known queue param, so Copy link never counts it as unrecognized', () => {
    expect(KNOWN_QUEUE_PARAMS).toContain('hide');
    const share = leadQueueShareParams(new URLSearchParams('state=IL&hide=none'), { stateFilter: 'IL' });
    expect(share.search).toBe('?state=IL');
    expect(share.omitted).toEqual([]);
  });
});
