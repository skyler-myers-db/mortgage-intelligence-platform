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
import { fixture, json, type FixtureEntry, type FixtureRequest } from '../mockApi';
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

/**
 * The preview under `marketing_eligibility: 'Eligible only'`, the Lead Queue's
 * default contactability that Portfolio Builder, Segment Intelligence and
 * Home's approval-queue banner request. The real endpoint applies the
 * eligibility predicate to the one aggregate statement, so every headline
 * count is the contactable subset (reference.ts TOTALS.contactable*), while
 * the lifecycle counts (approved / in outreach) are the same rows either way.
 */
export const CONTACTABLE_PORTFOLIO_PREVIEW: PortfolioPreview = {
  ...PORTFOLIO_PREVIEW,
  marketable_population: TOTALS.contactable,
  high_intent_leads: TOTALS.contactableInTheMoney,
  top_tier_opportunities: TOTALS.contactableHighOpportunity,
  offers_recommended: TOTALS.contactableOffersRecommended,
  total_current_lien_balance_usd: TOTALS.contactable * 318400,
  offer_mix: [
    { offer_code: 'refi_plus_heloc', borrower_count: 890 },
    { offer_code: 'refi', borrower_count: 720 },
    { offer_code: 'heloc', borrower_count: 490 },
    { offer_code: 'cash_out', borrower_count: 270 },
    { offer_code: 'purchase', borrower_count: 150 },
    { offer_code: 'retention', borrower_count: 90 },
  ],
  trends: {
    marketable_population: trend(TOTALS.contactable, 1.2),
    high_intent_leads: trend(TOTALS.contactableInTheMoney, 4.6),
    top_tier_opportunities: trend(TOTALS.contactableHighOpportunity, 1.5),
    offers_recommended: trend(TOTALS.contactableOffersRecommended, 3.1),
  },
};

function previewFor(request: FixtureRequest): PortfolioPreview {
  const criteria = (request.body as { criteria?: { marketing_eligibility?: unknown } } | null)?.criteria;
  return criteria?.marketing_eligibility === 'Eligible only' ? CONTACTABLE_PORTFOLIO_PREVIEW : PORTFOLIO_PREVIEW;
}

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
  // The HeadlineKpis readings the highlights above are cut from (backend
  // HeadlineKpis / KpiDeltas: every count is required, deltas = current -
  // baseline). Home renders the highlights, not these objects.
  current: {
    marketable_population: TOTALS.addressable,
    refi_economics_screen: TOTALS.inTheMoney,
    high_opportunity: TOTALS.highOpportunity,
    offers_available: TOTALS.offersRecommended,
    offers_recommended: TOTALS.offersRecommended,
    avg_opportunity_score: 81,
  },
  baseline: {
    marketable_population: 88491,
    refi_economics_screen: 10590,
    high_opportunity: 4059,
    offers_available: 6060,
    offers_recommended: 6060,
    avg_opportunity_score: 80.6,
  },
  deltas: {
    marketable_population: TOTALS.addressable - 88491,
    refi_economics_screen: 2250,
    high_opportunity: 61,
    offers_available: 190,
    offers_recommended: 190,
    avg_opportunity_score: 0.4,
  },
  current_source: 'mip.semantics.portfolio_headline_metric_view',
  baseline_source: 'mip_app.kpi_snapshots',
};

export const SALES_TEAM: SalesTeamMember[] = [
  { email: 'lo.alpha@summit.example', display_label: 'Loan Officer A', role: 'loan_officer', region: 'Midwest', manager_email: 'manager@summit.example', capacity_per_day: 25, active: true },
  { email: 'lo.bravo@summit.example', display_label: 'Loan Officer B', role: 'loan_officer', region: 'South', manager_email: 'manager@summit.example', capacity_per_day: 20, active: true },
  { email: 'manager@summit.example', display_label: 'Sales Manager', role: 'sales_manager', region: 'National', manager_email: null, capacity_per_day: 0, active: true },
];

export const portfolioFixtures: FixtureEntry[] = [
  fixture('POST', '/api/portfolio/preview', (request) => json<PortfolioPreview>(previewFor(request))),
  fixture('POST', '/api/portfolio/campaign-recommendation', () =>
    // The backend's reviewed fallback (services/campaign_intelligence.py
    // `_fallback` / `_evidence`), so every CampaignRecommendationResponse
    // validator holds: the audience summary and strategy carry no numbers
    // (numeric facts live in `evidence`), each body ends on a review
    // invitation, and no evidence label is name-shaped.
    json<CampaignRecommendationResponse>({
      generation_mode: 'reviewed_fallback',
      generator_label: 'Reviewed campaign framework',
      performance_status: 'insufficient_sample',
      audience_summary:
        'The selected audience is led by borrowers with refinance economics and usable home equity and is ready for a controlled message test.',
      strategy:
        'Compare the reviewed benefit and guidance frames with one clear review invitation and a randomized holdout.',
      variants: [
        {
          variant_name: 'Benefit-led',
          subject: 'See whether your mortgage options have improved',
          body: 'A refinance and home-equity review can help you compare your current mortgage with other available options. A loan officer can explain the tradeoffs in plain language. Would you like to schedule a review?',
          hypothesis: 'A specific potential benefit and a low-friction review invitation will earn more qualified responses than a generic rate message.',
          provenance_token: null,
        },
        {
          variant_name: 'Guidance-led',
          subject: 'A clearer way to review your current mortgage',
          body: 'Mortgage choices can change as your balance, equity, and goals change. A loan officer can walk through whether a refinance fits your situation, with no assumption that changing your loan is the right answer. Would a review be useful?',
          hypothesis: 'Plain-language guidance and an explicit no-pressure frame will improve trust and response quality for borrowers who are not ready for a product-led message.',
          provenance_token: null,
        },
      ],
      holdout_pct: 10,
      evidence: [
        { label: 'Eligible cohort', value: `${TOTALS.contactable.toLocaleString('en-US')} borrowers`, source_asset: 'mip.semantics.portfolio_headline_metric_view' },
        { label: 'Average rate spread', value: '112 bps', source_asset: 'mip.gold.borrower_360' },
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
