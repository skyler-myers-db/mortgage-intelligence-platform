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
import type { GrowthAgentWatchlistSummaryResponse } from '../../../../src/types/growthAgent';
import type { HomeAttributionMeasure, HomeSummaryAttributionResponse } from '../../../../src/types/homeAttribution';
import { fixture, json, type FixtureEntry, type FixtureRequest } from '../mockApi';
import { SNAPSHOT_AT, STATES, TOTALS } from './reference';

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
  headline:
    'Since your last login: +44 listed for sale, -31 competitor liens, +2,250 refi candidates, +1.5% high-opportunity, +190 primary offer paths.',
  phrasing_source: 'deterministic',
  phrasing_fallback_reason: 'genie_not_configured',
  // SUMMARY_DELTA_MEASURES order (backend/services/home_summary.py, flow-05):
  // the two event measures first, then refi, high-opportunity, offer paths.
  highlights: [
    { measure: 'listed_for_sale', label: 'listed for sale', display: '+44', value_token: '+44', current: 1412, baseline: 1368, delta: 44, delta_pct: 3.2 },
    { measure: 'competitor_lien', label: 'competitor liens', display: '-31', value_token: '-31', current: 9870, baseline: 9901, delta: -31, delta_pct: -0.3 },
    { measure: 'refi_economics_screen', label: 'refi candidates', display: '+2,250', value_token: '+2,250', current: TOTALS.inTheMoney, baseline: 10590, delta: 2250, delta_pct: 21.2 },
    { measure: 'high_opportunity', label: 'high-opportunity', display: '+1.5%', value_token: '+1.5%', current: TOTALS.highOpportunity, baseline: 4059, delta: 61, delta_pct: 1.5 },
    { measure: 'offers_recommended', label: 'primary offer paths', display: '+190', value_token: '+190', current: TOTALS.offersRecommended, baseline: 6060, delta: 190, delta_pct: 3.1 },
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
    listed_for_sale: 1412,
    competitor_lien: 9870,
  },
  baseline: {
    marketable_population: 88491,
    refi_economics_screen: 10590,
    high_opportunity: 4059,
    offers_available: 6060,
    offers_recommended: 6060,
    avg_opportunity_score: 80.6,
    listed_for_sale: 1368,
    competitor_lien: 9901,
  },
  deltas: {
    marketable_population: TOTALS.addressable - 88491,
    refi_economics_screen: 2250,
    high_opportunity: 61,
    offers_available: 190,
    offers_recommended: 190,
    avg_opportunity_score: 0.4,
    listed_for_sale: 44,
    competitor_lien: -31,
  },
  current_source: 'mip.semantics.portfolio_headline_metric_view',
  baseline_source: 'mip_app.kpi_snapshots',
};

/**
 * Home's watchlist briefings (wow-ai-4): two saved watchlists, one with a
 * three-run series and one paused on its first run, under a scheduler that
 * ships paused (the honest default of every bundle schedule).
 */
export const HOME_WATCHLIST_SUMMARY: GrowthAgentWatchlistSummaryResponse = {
  scheduler: { state: 'paused', reason: 'job_schedule' },
  watchlists: [
    {
      monitor_id: 'fixture-home-monitor-0001',
      workflow_id: 'daily_refi_brief',
      name: 'Daily refi brief',
      cadence: 'daily',
      status: 'active',
      run_count: 3,
      last_run_at: SNAPSHOT_AT,
      previous_run_at: SNAPSHOT_AT,
      actionable_total: TOTALS.contactableInTheMoney,
      previous_actionable_total: TOTALS.contactableInTheMoney - 12,
      actionable_delta: 12,
      actionable_avg_score: 84.6,
      previous_actionable_avg_score: 83.9,
      avg_score_delta: 0.7,
      recent_actionable_totals: [TOTALS.contactableInTheMoney - 20, TOTALS.contactableInTheMoney - 12, TOTALS.contactableInTheMoney],
    },
    {
      monitor_id: 'fixture-home-monitor-0002',
      workflow_id: 'listing_watch',
      name: 'Listed homes, purchase path',
      cadence: 'weekly',
      status: 'paused',
      run_count: 1,
      last_run_at: SNAPSHOT_AT,
      previous_run_at: null,
      actionable_total: 211,
      previous_actionable_total: null,
      actionable_delta: null,
      actionable_avg_score: 71.2,
      previous_actionable_avg_score: null,
      avg_score_delta: null,
      recent_actionable_totals: [211],
    },
  ],
};

/** Per-state change in the funnel snapshots behind the Delta Explainer (wow-ai-3); 30 more are unattributed. */
const ATTRIBUTION_CHANGES: Readonly<Record<string, number>> = { IL: 620, TX: 540, CA: 410, FL: 300, AZ: 180, WA: 90, CO: 40, GA: 20 };
const ATTRIBUTION_UNATTRIBUTED = 30;
const ATTRIBUTION_LABELS: Readonly<Record<HomeAttributionMeasure, string>> = {
  refi_economics_screen: 'refi candidates',
  high_opportunity: 'high-opportunity',
  offers_recommended: 'primary offer paths',
  listed_for_sale: 'listed for sale',
  competitor_lien: 'competitor liens',
};

/**
 * GET /api/home/summary/attribution: whole-book state counts that reconcile
 * (baseline + every state change + the unattributed 30 = now), the par prints
 * of both weeks and an offer-rules change after the baseline. No ids.
 */
export function homeAttribution(measureParam: string | null, baseline: string | null): HomeSummaryAttributionResponse {
  const measure = (measureParam && measureParam in ATTRIBUTION_LABELS ? measureParam : 'refi_economics_screen') as HomeAttributionMeasure;
  if (measure === 'competitor_lien') return notSnapshottedAttribution(measure, baseline);
  const states = STATES.map((state) => {
    const change = ATTRIBUTION_CHANGES[state.code] ?? 0;
    return { state: state.code, baseline_count: state.inTheMoney - change, current_count: state.inTheMoney, change };
  }).sort((a, b) => Math.abs(b.change) - Math.abs(a.change) || a.state.localeCompare(b.state));
  const attributed = states.reduce((sum, row) => sum + row.change, 0);
  const current = states.reduce((sum, row) => sum + row.current_count, 0);
  const totalChange = attributed + ATTRIBUTION_UNATTRIBUTED;
  return {
    measure,
    label: ATTRIBUTION_LABELS[measure],
    population: 'addressable',
    requested_baseline_date: baseline ?? '2026-07-09',
    baseline_snapshot_date: '2026-07-08',
    current_snapshot_date: SNAPSHOT_AT.slice(0, 10),
    nearest_snapshot: true,
    baseline_total: current - totalChange,
    current_total: current,
    total_change: totalChange,
    states,
    unattributed_change: ATTRIBUTION_UNATTRIBUTED,
    rate: { series_id: 'MORTGAGE30US', baseline_week: '2026-07-06', baseline_pct: 6.7, latest_week: '2026-07-13', latest_pct: 6.62 },
    offer_rules_last_updated: '2026-07-20T09:00:00',
    offer_rules_changed_since_baseline: true,
    sources: ['mip.gold.funnel_snapshot_daily', 'mip.gold.rate_window_weekly', 'mip.ref.offer_rules_config'],
    note: 'These coincided with the change; they are not shown as causes.',
    snapshotted: true,
  };
}

/**
 * The route's answer for a measure gold.funnel_snapshot_daily does not attribute
 * per state yet (competitor liens, wow-ai-3): nothing read, `snapshotted: false`.
 */
export function notSnapshottedAttribution(measure: HomeAttributionMeasure, baseline: string | null): HomeSummaryAttributionResponse {
  return {
    measure,
    label: ATTRIBUTION_LABELS[measure],
    population: 'addressable',
    requested_baseline_date: baseline ?? '2026-07-09',
    baseline_snapshot_date: null,
    current_snapshot_date: null,
    nearest_snapshot: false,
    baseline_total: null,
    current_total: null,
    total_change: null,
    states: [],
    unattributed_change: null,
    rate: { series_id: 'MORTGAGE30US', baseline_week: null, baseline_pct: null, latest_week: null, latest_pct: null },
    offer_rules_last_updated: null,
    offer_rules_changed_since_baseline: null,
    sources: [],
    note: 'These coincided with the change; they are not shown as causes.',
    snapshotted: false,
  };
}

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
  fixture('GET', '/api/growth-agent/monitors/summary', () =>
    json<GrowthAgentWatchlistSummaryResponse>(HOME_WATCHLIST_SUMMARY)),
  fixture('GET', '/api/home/summary/attribution', ({ query }) =>
    json<HomeSummaryAttributionResponse>(homeAttribution(query.get('measure'), query.get('baseline')))),
];
