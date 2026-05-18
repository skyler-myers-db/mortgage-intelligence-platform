import { describe, expect, it } from 'vitest';
import {
  BASE_DEFAULT_FILTERS,
  DEFAULT_CAMPAIGN_SETUP,
  buildDefaultCampaignSetup,
  buildCampaignConfig,
  buildLeadQueueUrlFromFilters,
  buildPreviewCriteria,
  buildUrlFromFilters,
  defaultGeographyForOptions,
  isPublicLenderRef,
  parseFiltersFromUrl,
  parseStateCodesFromUrl,
} from './portfolio-builder.logic';

const states = [
  { state_code: 'TX', state_name: 'Texas' },
  { state_code: 'CA', state_name: 'California' },
  { state_code: 'IL', state_name: 'Illinois' },
];

describe('portfolio builder URL helpers', () => {
  it('round-trips public filters and selected states without private lender refs', () => {
    const searchParams = new URLSearchParams({
      states: 'tx,CA,CA,private-lender',
      product: 'Cash-out',
      min_equity_pct_label: '≥ 25%',
      target_lender_ref: 'raw_lender_123',
    });

    expect(parseStateCodesFromUrl(searchParams, states)).toEqual(['TX', 'CA']);
    expect(parseFiltersFromUrl(searchParams, BASE_DEFAULT_FILTERS)).toMatchObject({
      product: 'Cash-out',
      min_equity_pct_label: '≥ 25%',
      target_lender_ref: 'All',
    });
  });

  it('accepts configured tenant lender refs only when the backend advertises them', () => {
    const searchParams = new URLSearchParams({
      target_lender_ref: 'Acme Mortgage',
    });

    expect(parseFiltersFromUrl(searchParams, BASE_DEFAULT_FILTERS)).toMatchObject({
      target_lender_ref: 'All',
    });
    expect(parseFiltersFromUrl(
      searchParams,
      BASE_DEFAULT_FILTERS,
      ['All', 'Acme Mortgage'],
    )).toMatchObject({
      target_lender_ref: 'Acme Mortgage',
    });
    expect(isPublicLenderRef('Acme Mortgage', ['All', 'Acme Mortgage'])).toBe(true);
    expect(isPublicLenderRef('raw_lender_123', ['All', 'Acme Mortgage'])).toBe(false);
  });

  it('keeps default URL compact and carries criteria states only when selected', () => {
    const url = buildUrlFromFilters(BASE_DEFAULT_FILTERS, BASE_DEFAULT_FILTERS, []);

    expect(url.toString()).toBe('');
    expect(buildPreviewCriteria(BASE_DEFAULT_FILTERS, [])).not.toHaveProperty('states');
    expect(buildPreviewCriteria(BASE_DEFAULT_FILTERS, ['TX'])).toMatchObject({
      states: ['TX'],
    });
  });

  it('builds lead queue links from committed public filters', () => {
    const url = buildLeadQueueUrlFromFilters(
      {
        ...BASE_DEFAULT_FILTERS,
        target_lender_ref: 'Competitor A',
        product: 'Retention',
      },
      ['IL'],
    );

    expect(url).toContain('/lead-queue?');
    expect(url).toContain('states=IL');
    expect(url).toContain('target_lender_ref=Competitor+A');
    expect(url).toContain('product=Retention');
  });

  it('collapses all selected states to the whole-footprint default', () => {
    const searchParams = new URLSearchParams({ states: 'TX,CA,IL' });

    expect(parseStateCodesFromUrl(searchParams, states)).toEqual([]);
    expect(defaultGeographyForOptions(['All 3 states', 'Texas'])).toBe('All 3 states');
  });
});

describe('portfolio campaign config', () => {
  it('derives default campaign copy from the configured lender label', () => {
    const setup = buildDefaultCampaignSetup('Acme Mortgage');

    expect(setup.subjectA).toContain('Acme Mortgage');
    expect(setup.bodyA).toContain('Acme Mortgage');
    expect(setup.subjectA).not.toContain('Summit Mortgage');
  });

  it('preserves suppression, holdout, cascade, and ROI defaults', () => {
    const config = buildCampaignConfig(DEFAULT_CAMPAIGN_SETUP);

    expect(config.suppression_policy).toMatchObject({
      default: 'eligible_only',
      frequency_cap_days: 30,
    });
    expect(config.channel_cascade).toEqual([
      { channel: 'email', step: 1 },
      { channel: 'sms', step: 2, after_days: 3 },
      { channel: 'direct_mail', step: 3, after_days: 10 },
    ]);
    expect(config.holdout).toMatchObject({ method: 'hash_modulo', size_pct: 10 });
    expect(config.roi_assumptions).toMatchObject({
      budget_usd: null,
      source: 'operator_configured',
    });
  });
});
