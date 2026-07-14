import { describe, expect, it } from 'vitest';
import {
  BASE_DEFAULT_FILTERS,
  DEFAULT_CAMPAIGN_SETUP,
  buildDefaultCampaignSetup,
  buildCampaignConfig,
  normalizeCampaignNumericValue,
  buildLeadQueueUrlFromFilters,
  buildPreviewCriteria,
  buildSegmentIntelligenceUrlFromFilters,
  buildUrlFromFilters,
  campaignCriteriaSummary,
  campaignCopyValidationError,
  groupSavedCampaigns,
  defaultGeographyForOptions,
  parseFiltersFromUrl,
  parseStateCodesFromUrl,
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
  it('normalizes visible numeric controls to the same bounds used by saved config', () => {
    expect(normalizeCampaignNumericValue('holdoutPct', '95')).toBe('50');
    expect(normalizeCampaignNumericValue('holdoutPct', '-3')).toBe('0');
    expect(normalizeCampaignNumericValue('holdoutPct', 'not-a-number')).toBe('10');
    for (const field of ['budget', 'emailCost', 'smsCost', 'mailCost'] as const) {
      const max = field === 'budget' ? 10_000_000 : 1_000;
      expect(normalizeCampaignNumericValue(field, '')).toBe('');
      expect(normalizeCampaignNumericValue(field, '-1')).toBe('0');
      expect(normalizeCampaignNumericValue(field, String(max + 1))).toBe(String(max));
      expect(normalizeCampaignNumericValue(field, 'not-a-number')).toBe('');
    }
  });

  it('does not seed generic copy or invented channel costs before intelligence loads', () => {
    const setup = buildDefaultCampaignSetup('Acme Mortgage');

    expect(setup.subjectA).toBe('');
    expect(setup.subjectB).toBe('');
    expect(setup.bodyA).toBe('');
    expect(setup.bodyB).toBe('');
    expect(setup.emailCost).toBe('');
    expect(setup.smsCost).toBe('');
    expect(setup.mailCost).toBe('');
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
      cost_per_contact_usd: {},
      source: 'operator_configured',
    });
    expect(config.household_dedup).toEqual({
      enabled: false,
      dedupe_unit: 'borrower',
      primary_contact_strategy: 'highest_opportunity_eligible',
    });
    expect(config.message_variants).toEqual([]);
    expect(campaignCopyValidationError(DEFAULT_CAMPAIGN_SETUP)).toBeNull();
  });

  it('persists only channel costs that the operator actually configured', () => {
    const config = buildCampaignConfig({
      ...DEFAULT_CAMPAIGN_SETUP,
      emailCost: '1.25',
      smsCost: '',
      mailCost: '0.86',
    });

    expect(config.roi_assumptions).toMatchObject({
      cost_per_contact_usd: { email: 1.25, direct_mail: 0.86 },
    });
  });

  it('clamps direct campaign config inputs to every numeric boundary', () => {
    const config = buildCampaignConfig({
      ...DEFAULT_CAMPAIGN_SETUP,
      holdoutPct: '500',
      budget: '10000001',
      emailCost: '-1',
      smsCost: '1001',
      mailCost: '0',
    });

    expect(config.holdout).toMatchObject({ size_pct: 50 });
    expect(config.roi_assumptions).toMatchObject({
      budget_usd: 10_000_000,
      cost_per_contact_usd: { email: 0, sms: 1_000, direct_mail: 0 },
    });
  });

  it('persists the applied campaign-intelligence provenance on every variant', () => {
    const config = buildCampaignConfig({
      ...DEFAULT_CAMPAIGN_SETUP,
      subjectA: 'Review your mortgage options',
      subjectB: 'A clearer mortgage review',
      bodyA: 'A loan officer can explain the available options. Would a review be useful?',
      bodyB: 'Compare the available choices and tradeoffs with a loan officer.',
      generationMode: 'supervisor',
      generatorLabel: 'Supervisor-generated recommendation',
      provenanceTokenA: 'signed-benefit-provenance-token-0000000000001',
      provenanceTokenB: 'signed-guidance-provenance-token-000000000001',
    });

    expect(config.message_variants).toHaveLength(2);
    expect(config.message_variants).toEqual([
      expect.objectContaining({
        generation_mode: 'supervisor',
        generator_label: 'Supervisor-generated recommendation',
        provenance_token: 'signed-benefit-provenance-token-0000000000001',
      }),
      expect.objectContaining({
        generation_mode: 'supervisor',
        generator_label: 'Supervisor-generated recommendation',
        provenance_token: 'signed-guidance-provenance-token-000000000001',
      }),
    ]);
  });

  it('blocks partially entered copy instead of silently dropping it or sending blanks', () => {
    const setup = {
      ...DEFAULT_CAMPAIGN_SETUP,
      subjectA: 'Review your mortgage options',
    };

    expect(campaignCopyValidationError(setup)).toBe(
      'Benefit-led copy needs both a subject and message before this build can be saved.',
    );
    expect(buildCampaignConfig(setup).message_variants).toEqual([
      expect.objectContaining({ variant_name: 'A', subject: 'Review your mortgage options' }),
      expect.objectContaining({ variant_name: 'B', subject: '', body: '' }),
    ]);
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

  it('does not double-print eligibility from the suppression policy', () => {
    const summary = campaignCriteriaSummary({
      criteria: { marketing_eligibility: 'Eligible only' },
      suppression_policy: { default: 'eligible_only' },
    } as never);
    // "Eligible only" (criterion) and "eligible only" (policy) collapse to one.
    expect(summary.toLowerCase().match(/eligible only/g)?.length ?? 0).toBe(1);
  });
});

describe('groupSavedCampaigns', () => {
  const draft = (name: string, campaign_id: string, updated_at: string) => ({
    campaign_id,
    name,
    owner_email: 'lo@summit.example',
    status: 'draft' as const,
    criteria: {},
    updated_at,
  });

  it('collapses semantically identical drafts and uses the latest representative', () => {
    const rows = groupSavedCampaigns([
      draft('Genie strategy draft', 'c1', '2026-07-08T10:00:00Z'),
      draft('Genie strategy draft', 'c3', '2026-07-10T10:00:00Z'),
      draft('Genie strategy draft', 'c2', '2026-07-09T10:00:00Z'),
    ] as never);
    expect(rows).toHaveLength(1);
    expect(rows[0].draftCount).toBe(3);
    expect(rows[0].campaign.campaign_id).toBe('c3');
    expect(rows[0].latestAt).toBe('2026-07-10T10:00:00Z');
  });

  it('keeps same-name drafts with distinct criteria as separately inspectable rows', () => {
    const rows = groupSavedCampaigns([
      { ...draft('Genie strategy draft', 'c1', '2026-07-08T10:00:00Z'), criteria: { states: ['IL'] } },
      { ...draft('Genie strategy draft', 'c2', '2026-07-09T10:00:00Z'), criteria: { states: ['TX'] } },
      { ...draft('Genie strategy draft', 'c3', '2026-07-10T10:00:00Z'), criteria: { states: ['IL'] } },
    ] as never);

    expect(rows).toHaveLength(2);
    expect(rows.map((row) => campaignCriteriaSummary(row.campaign))).toEqual(['IL', 'TX']);
    expect(rows[0].draftCount).toBe(2);
    expect(rows[0].campaign.campaign_id).toBe('c3');
    expect(rows[1].draftCount).toBe(1);
  });

  it('keeps non-draft and uniquely-named campaigns as their own rows', () => {
    const rows = groupSavedCampaigns([
      draft('Genie strategy draft', 'c1', '2026-07-08T10:00:00Z'),
      { ...draft('Genie strategy draft', 'c2', '2026-07-09T10:00:00Z'), status: 'approved' as const },
      draft('Retention refi push', 'c3', '2026-07-09T10:00:00Z'),
    ] as never);
    expect(rows).toHaveLength(3);
    expect(rows.every((row) => row.draftCount === 1)).toBe(true);
  });
});

describe('projected economics formatting', () => {
  it('formats compact USD across magnitudes (incl. trillions)', () => {
    expect(formatUsdCompact(244_800)).toBe('$245K');
    expect(formatUsdCompact(16_320_000)).toBe('$16.3M');
    expect(formatUsdCompact(2_300_000_000)).toBe('$2.3B');
    expect(formatUsdCompact(1_500_000_000_000)).toBe('$1.5T');
    expect(formatUsdCompact(-1_680)).toBe('-$2K');
    expect(formatUsdCompact(940)).toBe('$940');
  });
});
