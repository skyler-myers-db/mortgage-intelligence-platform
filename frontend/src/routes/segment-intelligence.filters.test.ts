import { describe, expect, it } from 'vitest';
import type { LeadSummary } from '../types';
import {
  applySecondaryLeadFilters,
  CASHOUT_OPTIONS,
  CHIP_FILTER_PARAM,
  chipFiltersAreDefault,
  chipFiltersFromSearch,
  CONSENT_OPTIONS,
  CONTACTABILITY_OPTIONS,
  INITIAL_FILTERS,
  LIEN_OPTIONS,
  OCCUPANCY_OPTIONS,
  OWNER_LINK_OPTIONS,
  PURCHASE_OPTIONS,
  RECENCY_OPTIONS,
  searchParamsWithChipFilter,
  searchParamsWithoutSegmentFilters,
  secondaryPortfolioCriteria,
  segmentFilterSearchKey,
  type ChipFilterKey,
  type ChipFilters,
} from './segment-intelligence.filters';
import { LENDER_RELATIONSHIP_OPTIONS } from '../lib/lenderFilters';

const TARGETS = ['All', 'Competitor A', 'Competitor B'];
const FOOTPRINT = ['IL', 'TX', 'CA'];

const OPTIONS_BY_KEY: Record<Exclude<ChipFilterKey, 'location' | 'targetLenderRef'>, readonly string[]> = {
  demographics: OCCUPANCY_OPTIONS,
  lenderRelationship: LENDER_RELATIONSHIP_OPTIONS,
  lien: LIEN_OPTIONS,
  ownerLink: OWNER_LINK_OPTIONS,
  purchase: PURCHASE_OPTIONS,
  cashout: CASHOUT_OPTIONS,
  contactability: CONTACTABILITY_OPTIONS,
  consent: CONSENT_OPTIONS,
  recency: RECENCY_OPTIONS,
};

function parse(query: string): ChipFilters {
  return chipFiltersFromSearch(new URLSearchParams(query), { targetLenderOptions: TARGETS, locationCodes: FOOTPRINT });
}

describe('segment filters: parse', () => {
  it('reads an empty URL as the defaults', () => {
    const filters = parse('');
    expect(filters).toEqual(INITIAL_FILTERS);
    expect(chipFiltersAreDefault(filters)).toBe(true);
  });

  it('reads every secondary filter from its portfolio-criteria key', () => {
    const filters = parse(new URLSearchParams({
      state: 'tx',
      occupancy: 'Non-owner-occupied',
      lender_relationship: 'Competitor customer',
      target_lender_ref: 'Competitor B',
      lien_status: 'Open HELOC',
      owner_link: 'Multi-property (2-4)',
      purchase_intent: 'Listed for sale',
      min_equity_pct_label: '≥ 25%',
      marketing_eligibility: 'Suppressed only',
      consent_status: 'Opt-out',
      recency: 'Untouched 60d',
    }).toString());
    expect(filters).toEqual({
      location: 'TX',
      demographics: 'Non-owner-occupied',
      lenderRelationship: 'Competitor customer',
      targetLenderRef: 'Competitor B',
      lien: 'Open 2nd lien / HELOC',
      ownerLink: 'Multi-property (2-4)',
      purchase: 'Listed for sale',
      cashout: 'Equity ≥ 25%',
      contactability: 'Suppressed only',
      consent: 'Opt-out',
      recency: 'Untouched 60d',
    });
    expect(chipFiltersAreDefault(filters)).toBe(false);
  });

  it('accepts the Lead Queue spellings of the same values', () => {
    const filters = parse('lien_status=Open+first+lien&min_equity_pct_label=%3E%3D+40%25&purchase_intent=Recent+permit+activity');
    expect(filters.lien).toBe('Open 1st lien only');
    expect(filters.cashout).toBe('Equity ≥ 40%');
    expect(filters.purchase).toBe('HELOC intent');
  });

  it('falls back to the default for values the route does not offer', () => {
    const filters = parse(new URLSearchParams({
      state: 'Texas',
      occupancy: 'drop table',
      lender_relationship: 'Wholesale partner',
      target_lender_ref: 'Wells Fargo Bank',
      lien_status: 'Open 1st lien only',
      marketing_eligibility: 'Everyone',
      recency: 'Untouched 7d',
    }).toString());
    expect(filters).toEqual(INITIAL_FILTERS);
  });

  it('checks the LOCATION code against the footprint once it has loaded', () => {
    expect(parse('state=NY').location).toBe('All');
    const beforeFootprint = chipFiltersFromSearch(new URLSearchParams('state=ny'), { targetLenderOptions: TARGETS });
    expect(beforeFootprint.location).toBe('NY');
  });
});

describe('segment filters: serialize', () => {
  it('round-trips every option of every filter through the URL', () => {
    for (const [key, options] of Object.entries(OPTIONS_BY_KEY) as Array<[ChipFilterKey, readonly string[]]>) {
      for (const option of options) {
        const url = searchParamsWithChipFilter(new URLSearchParams(), key, option);
        expect(parse(url.toString())[key], `${key}=${option}`).toBe(option);
        // Defaults are omitted, so the bare route stays parameter-free.
        expect(url.has(CHIP_FILTER_PARAM[key]), `${key}=${option}`).toBe(option !== INITIAL_FILTERS[key]);
      }
    }
    const location = searchParamsWithChipFilter(new URLSearchParams(), 'location', 'CA');
    expect(location.toString()).toBe('state=CA');
    expect(parse(location.toString()).location).toBe('CA');
    const target = searchParamsWithChipFilter(new URLSearchParams(), 'targetLenderRef', 'Competitor A');
    expect(parse(target.toString()).targetLenderRef).toBe('Competitor A');
  });

  it('writes the portfolio-criteria value, not the option label', () => {
    expect(searchParamsWithChipFilter(new URLSearchParams(), 'lien', 'Open 2nd lien / HELOC').get('lien_status')).toBe('Open HELOC');
    expect(searchParamsWithChipFilter(new URLSearchParams(), 'cashout', 'Equity ≥ 15%').get('min_equity_pct_label')).toBe('≥ 15%');
  });

  it('patches one key and preserves every other param, including ones other features own', () => {
    const base = new URLSearchParams('geo_state=TX&zip=77002&segment=itm&target_lender_ref=Competitor+Z&recency=Untouched+30d');
    const next = searchParamsWithChipFilter(base, 'consent', 'Opt-in');
    expect(next.get('consent_status')).toBe('Opt-in');
    expect(next.get('geo_state')).toBe('TX');
    expect(next.get('zip')).toBe('77002');
    expect(next.get('segment')).toBe('itm');
    // A value that could not be validated yet is left alone, never dropped.
    expect(next.get('target_lender_ref')).toBe('Competitor Z');
    const reset = searchParamsWithChipFilter(next, 'recency', 'Any');
    expect(reset.has('recency')).toBe(false);
    expect(base.get('recency')).toBe('Untouched 30d');
  });

  it('clears only the params the Segments filters own', () => {
    const base = new URLSearchParams('geo_state=TX&zip=77002&segment_codes=itm,equity&segment_mode=all&state=IL&occupancy=Owner-occupied&marketing_eligibility=Any');
    expect(searchParamsWithoutSegmentFilters(base).toString()).toBe('geo_state=TX&zip=77002');
  });

  it('keys memoization on owned params only, in a canonical order', () => {
    const a = segmentFilterSearchKey(new URLSearchParams('recency=Untouched+30d&segment=itm&geo_state=TX'));
    const b = segmentFilterSearchKey(new URLSearchParams('geo_state=CA&zip=90012&segment=itm&recency=Untouched+30d'));
    expect(a).toBe(b);
    expect(a).toBe('segment=itm&recency=Untouched+30d');
  });
});

describe('segment filters: server criteria and local predicates', () => {
  it('sends the default contactability, exactly as before the URL move', () => {
    expect(secondaryPortfolioCriteria(INITIAL_FILTERS)).toEqual({ marketing_eligibility: 'Eligible only' });
    expect(secondaryPortfolioCriteria({ ...INITIAL_FILTERS, contactability: 'Any' })).toEqual({});
  });

  it('maps option labels to the API criteria values and never sends LOCATION as a criterion', () => {
    expect(secondaryPortfolioCriteria({
      ...INITIAL_FILTERS,
      location: 'TX',
      lien: 'Open 1st lien only',
      cashout: 'Equity ≥ 40%',
      demographics: 'Owner-occupied',
    })).toEqual({
      occupancy: 'Owner-occupied',
      lien_status: 'Open 1st lien',
      min_equity_pct_label: '≥ 40%',
      marketing_eligibility: 'Eligible only',
    });
  });

  it('runs the secondary predicates on the returned rows', () => {
    const lead = (id: string, patch: Partial<LeadSummary>) => ({ borrower_id: id, equity_estimate: 0, ...patch }) as LeadSummary;
    const leads = [
      lead('B-000000000000A', { is_owner_occupied: true, equity_estimate: 400_000, listed_for_sale: true }),
      lead('B-000000000000B', { is_owner_occupied: false, equity_estimate: 60_000 }),
      lead('B-000000000000C', { is_owner_occupied: true, equity_estimate: 20_000, related_property_count: 6 }),
    ];
    const ids = (filters: Partial<ChipFilters>) =>
      applySecondaryLeadFilters(leads, { ...INITIAL_FILTERS, ...filters }).map((l) => l.borrower_id);
    expect(ids({})).toHaveLength(3);
    expect(ids({ demographics: 'Owner-occupied' })).toEqual(['B-000000000000A', 'B-000000000000C']);
    expect(ids({ cashout: 'Equity ≥ 15%' })).toEqual(['B-000000000000A', 'B-000000000000B']);
    expect(ids({ ownerLink: 'Portfolio investor (5+)' })).toEqual(['B-000000000000C']);
    expect(ids({ purchase: 'Listed for sale' })).toEqual(['B-000000000000A']);
  });
});
