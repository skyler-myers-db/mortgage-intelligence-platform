import { describe, expect, it } from 'vitest';
import {
  BASE_DEFAULT_FILTERS,
  DEFAULT_CAMPAIGN_SETUP,
  buildDefaultCampaignSetup,
  buildCampaignConfig,
  buildLeadQueueUrlFromFilters,
  buildPreviewCriteria,
  buildSegmentIntelligenceUrlFromFilters,
  buildUrlFromFilters,
  campaignCriteriaSummary,
  defaultGeographyForOptions,
  parseFiltersFromUrl,
  parseStateCodesFromUrl,
  DEFAULT_ROI_ASSUMPTIONS,
  projectRoi,
  formatUsdCompact,
} from './portfolio-builder.logic';
import { isPublicLenderRef } from '../lib/lenderFilters';

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
        owner_link: 'Portfolio investor (5+)',
        purchase_intent: 'HELOC intent',
      },
      ['IL'],
    );

    expect(url).toContain('/lead-queue?');
    expect(url).toContain('states=IL');
    expect(url).toContain('target_lender_ref=Competitor+A');
    expect(url).toContain('product=Retention');
    expect(url).toContain('owner_link=Portfolio+investor+%285%2B%29');
    expect(url).toContain('purchase_intent=HELOC+intent');
  });

  it('builds segment intelligence links from committed lender overlay filters', () => {
    const url = buildSegmentIntelligenceUrlFromFilters(
      {
        ...BASE_DEFAULT_FILTERS,
        lender_relationship: 'Competitor customer',
        target_lender_ref: 'Competitor B',
        owner_link: 'Portfolio investor (5+)',
        purchase_intent: 'HELOC intent',
        product: 'Retention',
      },
      ['All', 'Competitor B'],
    );

    expect(url).toBe('/segment-intelligence?lender_relationship=Competitor+customer&target_lender_ref=Competitor+B&owner_link=Portfolio+investor+%285%2B%29&purchase_intent=HELOC+intent');
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
    expect(config.household_dedup).toEqual({
      enabled: false,
      dedupe_unit: 'borrower',
      primary_contact_strategy: 'highest_opportunity_eligible',
    });
  });

  it('makes household dedup opt-in at campaign time only', () => {
    const config = buildCampaignConfig({
      ...DEFAULT_CAMPAIGN_SETUP,
      marketHouseholdTogether: true,
    });

    expect(config.household_dedup).toEqual({
      enabled: true,
      dedupe_unit: 'household',
      primary_contact_strategy: 'highest_opportunity_eligible',
    });
  });

  it('summarizes saved lender-overlay campaigns with the target lien holder', () => {
    expect(campaignCriteriaSummary({
      criteria: {
        lender_relationship: 'Competitor customer',
        target_lender_ref: 'Competitor B',
        owner_link: 'Multi-property (2-4)',
        purchase_intent: 'Listed for sale',
        marketing_eligibility: 'Eligible only',
      },
      suppression_policy: { default: 'eligible_only' },
    } as never)).toContain('Competitor B');
    expect(campaignCriteriaSummary({
      criteria: {
        owner_link: 'Multi-property (2-4)',
        purchase_intent: 'Listed for sale',
      },
      suppression_policy: { default: 'eligible_only' },
    } as never)).toContain('Multi-property (2-4)');
    expect(campaignCriteriaSummary({
      criteria: {
        owner_link: 'Multi-property (2-4)',
        purchase_intent: 'Listed for sale',
      },
      suppression_policy: { default: 'eligible_only' },
    } as never)).toContain('Listed for sale');
  });
});

describe('campaign ROI projector (Buyer-Wow #7)', () => {
  it('computes the funnel arithmetic deterministically from visible assumptions', () => {
    // 1,200 leads × 4% → 48 fundings; × $340k → $16.32M volume;
    // × 1.5% → $244,800 gross; − 1,200 × $1.40 outreach ($1,680) → net.
    const p = projectRoi({ leads: 1200, ...DEFAULT_ROI_ASSUMPTIONS });
    expect(p.valid).toBe(true);
    expect(p.fundings).toBeCloseTo(48, 6);
    expect(p.originationVolumeUsd).toBeCloseTo(16_320_000, 2);
    expect(p.grossRevenueUsd).toBeCloseTo(244_800, 2);
    expect(p.outreachCostUsd).toBeCloseTo(1_680, 2);
    expect(p.netRevenueUsd).toBeCloseTo(243_120, 2);
  });

  it('treats non-numeric or out-of-range assumptions as invalid (no NaN headline)', () => {
    expect(projectRoi({ leads: 1000, ...DEFAULT_ROI_ASSUMPTIONS, responseRatePct: '' }).valid).toBe(false);
    expect(projectRoi({ leads: 1000, ...DEFAULT_ROI_ASSUMPTIONS, responseRatePct: '120' }).valid).toBe(false);
    expect(projectRoi({ leads: 1000, ...DEFAULT_ROI_ASSUMPTIONS, avgBalanceUsd: '-5' }).valid).toBe(false);
    expect(projectRoi({ leads: 1000, ...DEFAULT_ROI_ASSUMPTIONS, responseRatePct: 'abc' }).grossRevenueUsd).toBe(0);
  });

  it('zeroes out a no-lead build instead of projecting phantom revenue', () => {
    const p = projectRoi({ leads: 0, ...DEFAULT_ROI_ASSUMPTIONS });
    expect(p.valid).toBe(true);
    expect(p.grossRevenueUsd).toBe(0);
    expect(p.fundings).toBe(0);
  });

  it('rejects fat-fingered money inputs above a sane ceiling (no "$1000000.0B")', () => {
    // Consistent with clampPct: implausible inputs are INVALID (→ "—"), not
    // silently clamped, so the headline can never render a nonsense magnitude.
    expect(projectRoi({ leads: 1000, ...DEFAULT_ROI_ASSUMPTIONS, avgBalanceUsd: '100000000001' }).valid).toBe(false);
    expect(projectRoi({ leads: 1000, ...DEFAULT_ROI_ASSUMPTIONS, costPerLeadUsd: '100001' }).valid).toBe(false);
    // A generous-but-real jumbo balance still computes.
    expect(projectRoi({ leads: 1000, ...DEFAULT_ROI_ASSUMPTIONS, avgBalanceUsd: '5000000' }).valid).toBe(true);
  });

  it('formats compact USD across magnitudes (incl. trillions)', () => {
    expect(formatUsdCompact(244_800)).toBe('$245K');
    expect(formatUsdCompact(16_320_000)).toBe('$16.3M');
    expect(formatUsdCompact(2_300_000_000)).toBe('$2.3B');
    expect(formatUsdCompact(1_500_000_000_000)).toBe('$1.5T');
    expect(formatUsdCompact(-1_680)).toBe('-$2K');
    expect(formatUsdCompact(940)).toBe('$940');
  });
});
