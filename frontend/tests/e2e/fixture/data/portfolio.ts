/**
 * Portfolio, campaign and home-summary fixtures: the KPI preview behind Home,
 * Portfolio Builder and the Lead Queue header, the reviewed campaign
 * recommendation, saved campaigns, and the "since your last login" summary.
 */
import type {
  CampaignListResponse,
  CampaignPerformanceFunnelResponse,
  CampaignRecommendationResponse,
  HomeSummary,
  KpiTrend,
  PortfolioPreview,
  SalesTeamMember,
} from '../../../../src/types';
import { fixture, json, type FixtureEntry } from '../mockApi';
import { SNAPSHOT_AT, TOTALS } from './reference';

function trend(latest: number, growthPct: number): KpiTrend {
  // Seven points ending on the headline value, rising by `growthPct` overall.
  const series = Array.from({ length: 7 }, (_, index) =>
    Math.round(latest / (1 + (growthPct / 100) * ((6 - index) / 6))),
  );
  return { series, delta_pct: growthPct, direction: 'up', comparison_label: 'vs. 7 days ago', note: null };
}

export const PORTFOLIO_PREVIEW: PortfolioPreview = {
  marketable_population: TOTALS.addressable,
  campaign_build_contact_count: TOTALS.contactable,
  campaign_build_limit: 10000,
  campaign_build_eligible: true,
  high_intent_leads: TOTALS.inTheMoney,
  top_tier_opportunities: TOTALS.highOpportunity,
  offers_recommended: TOTALS.offersRecommended,
  avg_score: 81,
  avg_current_lien_balance_usd: 318400,
  avg_high_intent_lien_balance_usd: 342900,
  total_current_lien_balance_usd: TOTALS.addressable * 318400,
  avg_equity_pct: 46,
  avg_rate_spread_bps: 112,
  offer_mix: [
    { offer_code: 'refi_plus_heloc', borrower_count: 2140 },
    { offer_code: 'refi', borrower_count: 1710 },
    { offer_code: 'heloc', borrower_count: 1180 },
    { offer_code: 'cash_out', borrower_count: 640 },
    { offer_code: 'purchase', borrower_count: 360 },
    { offer_code: 'retention', borrower_count: 220 },
  ],
  data_refreshed_at: SNAPSHOT_AT,
  trends: {
    marketable_population: trend(TOTALS.addressable, 1.2),
    high_intent_leads: trend(TOTALS.inTheMoney, 4.6),
    top_tier_opportunities: trend(TOTALS.highOpportunity, 1.5),
    offers_recommended: trend(TOTALS.offersRecommended, 3.1),
  },
  trend_status: 'live',
  trend_note: null,
  day_zero: false,
  approved_count: TOTALS.approved,
  in_outreach_count: TOTALS.actioned,
};

export const HOME_SUMMARY: HomeSummary = {
  status: 'delta',
  previous_visit_at: '2026-07-09T14:30:00+00:00',
  baseline_snapshot_at: '2026-07-09T06:00:00+00:00',
  headline: 'Since your last login: +1.5% high-opportunity, +2,250 refi candidates, +190 offers available.',
  phrasing_source: 'deterministic',
  phrasing_fallback_reason: 'genie_not_configured',
  highlights: [
    { measure: 'high_opportunity', label: 'high-opportunity', display: '+1.5%', value_token: '+1.5%', current: TOTALS.highOpportunity, baseline: 4059, delta: 61, delta_pct: 1.5 },
    { measure: 'refi_economics_screen', label: 'refi candidates', display: '+2,250', value_token: '+2,250', current: TOTALS.inTheMoney, baseline: 10590, delta: 2250, delta_pct: 21.2 },
    { measure: 'offers_available', label: 'offers available', display: '+190', value_token: '+190', current: TOTALS.offersRecommended, baseline: 6060, delta: 190, delta_pct: 3.1 },
  ],
  current: {},
  baseline: {},
  deltas: {},
  current_source: 'mip.semantics.portfolio_headline_metric_view',
  baseline_source: 'mip_app.kpi_snapshots',
};

export const SALES_TEAM: SalesTeamMember[] = [
  { email: 'lo.alpha@summit.example', display_label: 'Loan Officer A', role: 'loan_officer', region: 'Midwest', manager_email: 'manager@summit.example', capacity_per_day: 25, active: true },
  { email: 'lo.bravo@summit.example', display_label: 'Loan Officer B', role: 'loan_officer', region: 'South', manager_email: 'manager@summit.example', capacity_per_day: 20, active: true },
  { email: 'manager@summit.example', display_label: 'Sales Manager', role: 'sales_manager', region: 'National', manager_email: null, capacity_per_day: 0, active: true },
];

export const portfolioFixtures: FixtureEntry[] = [
  fixture('POST', '/api/portfolio/preview', () => json<PortfolioPreview>(PORTFOLIO_PREVIEW)),
  fixture('POST', '/api/portfolio/campaign-recommendation', () =>
    json<CampaignRecommendationResponse>({
      generation_mode: 'reviewed_fallback',
      generator_label: 'Reviewed campaign template',
      performance_status: 'insufficient_sample',
      audience_summary: `${TOTALS.contactable.toLocaleString('en-US')} contact-eligible borrowers across the current footprint`,
      strategy: 'Review two governed variants before approval; a human approves every send.',
      variants: [
        { variant_name: 'Benefit-led', subject: 'Review your mortgage options', body: 'A draft for human review. No message is sent without approval.', hypothesis: 'Benefit framing lifts response for in-the-money borrowers.', provenance_token: null },
        { variant_name: 'Guidance-led', subject: 'A mortgage review may help', body: 'A second draft for human review. No message is sent without approval.', hypothesis: 'Guidance framing suits lower-intent borrowers.', provenance_token: null },
      ],
      holdout_pct: 10,
      evidence: [
        { label: 'Contact-eligible borrowers', value: TOTALS.contactable.toLocaleString('en-US'), source_asset: 'mip.gold.borrower_360' },
        { label: 'Average rate spread', value: '112 bps', source_asset: 'mip.semantics.portfolio_headline_metric_view' },
      ],
      warnings: [],
    }),
  ),
  fixture('GET', '/api/campaigns', () => json<CampaignListResponse>({ campaigns: [] })),
  fixture('GET', '/api/sales/campaign-performance', ({ query }) =>
    json<CampaignPerformanceFunnelResponse>({
      from_date: query.get('from') ?? '2026-04-16',
      to_date: query.get('to') ?? '2026-07-14',
      unique_leads_attempted: 100,
      unique_contacts_reached: 40,
      unique_application_starts: 12,
      unique_applications_submitted: 8,
      unique_closed_funded: 3,
      methodology: 'same_borrower_nested_funnel',
    }),
  ),
  fixture('GET', '/api/sales/team', () => json<SalesTeamMember[]>(SALES_TEAM)),
  fixture('GET', '/api/home/summary', () => json<HomeSummary>(HOME_SUMMARY)),
];
