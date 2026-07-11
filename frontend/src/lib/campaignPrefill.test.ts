import { describe, expect, it } from 'vitest';
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
