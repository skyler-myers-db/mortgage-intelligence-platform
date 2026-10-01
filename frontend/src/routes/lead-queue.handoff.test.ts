/**
 * The Lead Queue -> Portfolio Builder campaign handoff (audit tables-07 /
 * tables-02, D-approval-flow-a3). Pure: the queue's URL in, a Portfolio
 * Builder URL and the list of filters it cannot carry out.
 */
import { describe, expect, it } from 'vitest';
import { buildPortfolioBuilderUrlFromQueue, PB_FILTER_KEYS, PB_FILTER_OPTIONS } from './lead-queue.handoff';
import { parsePortfolioCriteria } from './lead-queue.filters';
import {
  BASE_DEFAULT_FILTERS,
  NON_GEO_FILTER_GROUPS,
  parseFiltersFromUrl,
  URL_FILTER_KEYS,
} from './portfolio-builder.logic';

const LENDERS = ['All', 'Summit Mortgage'];

function handoff(query: string) {
  const result = buildPortfolioBuilderUrlFromQueue(new URLSearchParams(query), LENDERS);
  const [path, search] = result.href.split('?');
  return { ...result, path, params: new URLSearchParams(search) };
}

/** What Portfolio Builder would read, against its own defaults. */
function pbFilters(params: URLSearchParams) {
  return parseFiltersFromUrl(params, BASE_DEFAULT_FILTERS, LENDERS);
}

/** The queue's effective criteria, with the queue's no-op values filled in. */
function queueEffective(query: string) {
  const criteria = parsePortfolioCriteria(new URLSearchParams(query), LENDERS) ?? {};
  const noOp: Record<string, string> = {
    occupancy: 'All', lien_status: 'Any', lender_relationship: 'All', target_lender_ref: 'All', owner_link: 'All',
    purchase_intent: 'All', product: 'All products', min_equity_pct_label: 'Any',
    marketing_eligibility: 'Eligible only', consent_status: 'Any', recency: 'Any',
  };
  return Object.fromEntries(URL_FILTER_KEYS.map((key) => [key, criteria[key] ?? noOp[key]]));
}

describe("the handoff's mirror of Portfolio Builder's filters", () => {
  it('has its URL filter keys, in its order', () => {
    expect([...PB_FILTER_KEYS]).toEqual([...URL_FILTER_KEYS]);
  });

  it('has its options for every key, and no list for the target lender alone', () => {
    const groups = Object.fromEntries(NON_GEO_FILTER_GROUPS.map((group) => [group.key, group.options]));
    for (const key of PB_FILTER_KEYS) {
      if (key === 'target_lender_ref') expect(groups[key]).toBeUndefined();
      else expect([...(PB_FILTER_OPTIONS[key] ?? [])], key).toEqual(groups[key]);
    }
    expect(PB_FILTER_OPTIONS.target_lender_ref).toBeNull();
    expect(Object.keys(groups).sort()).toEqual(PB_FILTER_KEYS.filter((key) => key !== 'target_lender_ref').sort());
  });
});

describe('buildPortfolioBuilderUrlFromQueue', () => {
  it('lands on /portfolio-builder and writes every Portfolio Builder filter key', () => {
    const { path, params, notCarried } = handoff('');
    expect(path).toBe('/portfolio-builder');
    expect([...params.keys()].sort()).toEqual([...URL_FILTER_KEYS].sort());
    expect(notCarried).toEqual([]);
  });

  it('a queue with no occupancy, lien or equity filter keeps Portfolio Builder from applying its narrower defaults', () => {
    const { params } = handoff('');
    // Portfolio Builder's defaults are Owner-occupied, Open 1st lien and >= 15%:
    // the queue had none of them, so the campaign must not narrow silently.
    expect(pbFilters(params)).toEqual(queueEffective(''));
    expect(params.get('occupancy')).toBe('All');
    expect(params.get('lien_status')).toBe('Any');
    expect(params.get('min_equity_pct_label')).toBe('Any');
    expect(params.get('marketing_eligibility')).toBe('Eligible only');
  });

  it("carries the queue's filters in Portfolio Builder's spelling", () => {
    const query = 'occupancy=Owner-occupied&lien_status=Open+first+lien&min_equity_pct_label=%3E%3D+15%25'
      + '&product=HELOC&recency=Untouched+60d';
    const { params, notCarried } = handoff(query);
    expect(params.get('min_equity_pct_label')).toBe('≥ 15%');
    expect(params.get('lien_status')).toBe('Open 1st lien');
    expect(params.get('occupancy')).toBe('Owner-occupied');
    expect(params.get('product')).toBe('HELOC');
    expect(params.get('recency')).toBe('Untouched 60d');
    expect(handoff('lien_status=Free+and+clear').params.get('lien_status')).toBe('Free & clear');
    expect(notCarried).toEqual([]);
  });

  it('upper-cases and de-duplicates the states (or the legacy state), and writes none when absent', () => {
    expect(handoff('states=il,TX,il').params.get('states')).toBe('IL,TX');
    expect(handoff('state=ca').params.get('states')).toBe('CA');
    expect(handoff('').params.has('states')).toBe(false);
  });

  it('drops and lists a lender reference it cannot verify, and a relationship Portfolio Builder cannot spell', () => {
    const { params, notCarried } = handoff('target_lender_ref=Some+Private+Bank&lender_relationship=Competitor');
    expect(params.get('target_lender_ref')).toBe('All');
    expect(params.get('lender_relationship')).toBe('All');
    expect(notCarried).toEqual(['lender relationship', 'target lender']);
    expect(handoff('target_lender_ref=Summit+Mortgage').params.get('target_lender_ref')).toBe('Summit Mortgage');
  });

  it('lists every queue filter Portfolio Builder cannot carry, by what it filters', () => {
    const query = [
      'segment_codes=itm,heloc', 'segment_mode=all', 'zips=60611', 'counties=17031', 'cities=CHICAGO~IL',
      'loan_product=jumbo', 'origination_channel=digital', 'cohort_id=0d1e2f3a-4b5c-4d6e-8f70-8192a3b4c5d6',
      'borrower_ids=B-0000000000001', 'campaign_id=11111111-1111-4111-8111-111111111111', 'variant_name=A',
      'approval_status=pending', 'assigned_to=me', 'aged_days=7', 'funnel_stage=contactable',
      'growth_agent_run_id=run-1', 'brand_new_param=1',
    ].join('&');
    expect(handoff(query).notCarried).toEqual([
      'segments', 'ZIPs', 'counties', 'cities', 'product type', 'origination channel', 'cohort', 'borrower list',
      'campaign binding', 'approval', 'assignee', 'aging', 'funnel stage', 'Growth Agent proof', 'brand_new_param',
    ]);
  });

  it("lists the score and spread filters another lane adds by raw name, with no label edit (fail-honest)", () => {
    const { notCarried } = handoff('min_opportunity_score=70&max_rate_spread_bps=150');
    expect(notCarried).toEqual(['min_opportunity_score', 'max_rate_spread_bps']);
  });

  it('never lists where the reader is in the table or its column preset', () => {
    expect(handoff('sort=equity&dir=asc&row=B-0000000000001&view=sales').notCarried).toEqual([]);
  });

  it('ignores a queue-only filter left at its no-op value', () => {
    expect(handoff('loan_product=All+loan+products&origination_channel=all').notCarried).toEqual([]);
  });
});
