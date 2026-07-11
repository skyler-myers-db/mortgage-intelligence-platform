import { describe, expect, it } from 'vitest';
// Shared parity table — the Python side (tests/unit/test_campaign_prefill.py)
// reads this exact file, so the accept/reject table cannot drift between the
// two validator mirrors.
import segmentParity from '../../../tests/fixtures/campaign_prefill_segment_parity.json';
import {
  PARAM_SOURCE,
  PREFILL_SOURCE,
  buildCampaignPrefillSearch,
  makeCampaignPrefill,
  parseCampaignPrefill,
} from './campaignPrefill';

/**
 * Contract tests for the geo → campaigns prefill encoding. Mirrors
 * tests/unit/test_campaign_prefill.py — the two sides of the wire contract
 * (backend/schemas/campaign_prefill.py and lib/campaignPrefill.ts) must
 * accept and reject the same payloads.
 */

function zipPrefill() {
  return makeCampaignPrefill({
    level: 'zip',
    state: 'IL',
    countyFips: '17031',
    countyName: 'Cook',
    zip: '60611',
    segmentCodes: ['itm', 'equity'],
    segmentMode: 'all',
    leadCount: 64,
    unattendedCount: 43,
  });
}

describe('campaignPrefill round-trip', () => {
  it('zip level round-trips through query params', () => {
    const original = zipPrefill();
    const parsed = parseCampaignPrefill(buildCampaignPrefillSearch(original));
    expect(parsed.error).toBeUndefined();
    expect(parsed.prefill).toEqual(original);
  });

  it('state level round-trips a minimal payload and uppercases the state', () => {
    const original = makeCampaignPrefill({ level: 'state', state: 'tx' });
    const sp = buildCampaignPrefillSearch(original);
    expect(sp.get('states')).toBe('TX');
    expect(sp.get('prefill_county_fips')).toBeNull();
    expect(sp.get('prefill_zip')).toBeNull();
    expect(sp.get('prefill_segments')).toBeNull();
    const parsed = parseCampaignPrefill(sp);
    expect(parsed.prefill).toEqual(original);
  });

  it('county level round-trips with county name', () => {
    const original = makeCampaignPrefill({
      level: 'county',
      state: 'IL',
      countyFips: '17031',
      countyName: 'Cook',
      segmentCodes: ['retention'],
    });
    const parsed = parseCampaignPrefill(buildCampaignPrefillSearch(original));
    expect(parsed.prefill).toEqual(original);
  });

  it('reuses the portfolio-builder states deep-link param + marker', () => {
    const sp = buildCampaignPrefillSearch(zipPrefill());
    expect(sp.get('states')).toBe('IL');
    expect(sp.get(PARAM_SOURCE)).toBe(PREFILL_SOURCE);
  });
});

describe('campaignPrefill decode behaviour', () => {
  it('returns null prefill when the marker is absent', () => {
    expect(parseCampaignPrefill(new URLSearchParams()).prefill).toBeNull();
    const ordinary = new URLSearchParams('states=IL&occupancy=All');
    const parsed = parseCampaignPrefill(ordinary);
    expect(parsed.prefill).toBeNull();
    expect(parsed.error).toBeUndefined();
  });

  it('reports an error for a marked but malformed payload', () => {
    const sp = buildCampaignPrefillSearch(zipPrefill());
    sp.set('prefill_county_fips', '1703'); // 4 digits
    const parsed = parseCampaignPrefill(sp);
    expect(parsed.prefill).toBeNull();
    expect(parsed.error).toMatch(/county_fips/);
  });

  it('rejects unknown segment codes', () => {
    const sp = buildCampaignPrefillSearch(zipPrefill());
    sp.set('prefill_segments', 'itm,granite');
    const parsed = parseCampaignPrefill(sp);
    expect(parsed.prefill).toBeNull();
    expect(parsed.error).toMatch(/granite/);
  });

  it('uses the first state of a multi-state deep link', () => {
    const sp = buildCampaignPrefillSearch(zipPrefill());
    sp.set('states', 'IL,CA');
    const parsed = parseCampaignPrefill(sp);
    expect(parsed.prefill?.state).toBe('IL');
  });

  it('rejects a negative count', () => {
    const sp = buildCampaignPrefillSearch(zipPrefill());
    sp.set('prefill_lead_count', '-5');
    const parsed = parseCampaignPrefill(sp);
    expect(parsed.prefill).toBeNull();
    expect(parsed.error).toMatch(/prefill_lead_count/);
  });
});

describe('campaignPrefill validation', () => {
  it('zip level requires zip and county fips', () => {
    expect(() =>
      makeCampaignPrefill({ level: 'zip', state: 'IL', countyFips: '17031' }),
    ).toThrow(/zip is required/);
    expect(() =>
      makeCampaignPrefill({ level: 'zip', state: 'IL', zip: '60611' }),
    ).toThrow(/county_fips is required/);
  });

  it('county level requires county fips', () => {
    expect(() => makeCampaignPrefill({ level: 'county', state: 'IL' })).toThrow(
      /county_fips is required/,
    );
  });

  it('state level rejects child geography', () => {
    expect(() =>
      makeCampaignPrefill({ level: 'state', state: 'IL', countyFips: '17031' }),
    ).toThrow(/must not carry/);
  });

  it('normalises and dedupes segment codes', () => {
    const prefill = makeCampaignPrefill({
      level: 'state',
      state: 'IL',
      segmentCodes: ['ITM', 'itm', ' equity '],
    });
    expect(prefill.segmentCodes).toEqual(['itm', 'equity']);
  });
});

// ---------------------------------------------------------------------------
// F1 parity: tests/unit/test_campaign_prefill.py reads the SAME fixture.
// Every case must produce the identical accept / reject / skip outcome on
// both sides of the wire contract, so a drive-by change to either validator
// (e.g. re-introducing display-layer alias folding here) breaks loudly
// instead of silently forking the contract.
// ---------------------------------------------------------------------------

interface ParityCase {
  input: string;
  expect: string;
}

const parityCases: ParityCase[] = segmentParity.cases;

describe('campaignPrefill segment validator parity (shared table)', () => {
  it('fixture has cases', () => {
    expect(parityCases.length).toBeGreaterThanOrEqual(10);
    const outcomes = new Set(parityCases.map((c) => c.expect));
    expect(outcomes.has('skip')).toBe(true);
    expect(outcomes.has('reject')).toBe(true);
  });

  it.each(parityCases)('input $input → $expect', (c) => {
    const build = () =>
      makeCampaignPrefill({ level: 'state', state: 'IL', segmentCodes: [c.input] });
    if (c.expect === 'reject') {
      expect(build).toThrow(/unknown segment code/);
    } else if (c.expect === 'skip') {
      expect(build().segmentCodes).toEqual([]);
    } else {
      expect(build().segmentCodes).toEqual([c.expect]);
    }
  });
});
