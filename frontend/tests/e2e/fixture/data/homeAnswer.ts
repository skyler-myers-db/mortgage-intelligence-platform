/**
 * Home answer-band payloads shaped like the LIVE book rather than the default
 * fixture's tidy six-code mix (lane home-answer, fix round 1).
 *
 * The default PORTFOLIO_PREVIEW carries six actionable offer codes and no
 * "Monitor for later" (`nurture`) row, so its offer mix sums exactly to
 * `offers_recommended` and its legend is six rows. The live preview always
 * returns all eight codes (backend databricks_portfolio.py) and `nurture` is
 * well over 90% of the offer decisions. These payloads are registered per
 * test with `mockApi.register('POST', '/api/portfolio/preview', ...)`, so the
 * default registry is unchanged.
 */
import type { HomeSummary, KpiTrend, PortfolioPreview } from '../../../../src/types';
import { json, type FixtureReply, type FixtureRequest } from '../mockApi';
import { CONTACTABLE_PORTFOLIO_PREVIEW, HOME_SUMMARY, PORTFOLIO_PREVIEW } from './portfolio';

type OfferMix = NonNullable<PortfolioPreview['offer_mix']>;

function trend(latest: number, growthPct: number): KpiTrend {
  const series = Array.from({ length: 7 }, (_, index) =>
    Math.round(latest / (1 + (growthPct / 100) * ((6 - index) / 6))),
  );
  return { series, delta_pct: growthPct, direction: 'up', comparison_label: 'vs. 7 days ago', note: null };
}

/** All seven actionable offer codes, every one non-zero. */
export const LIVE_ACTIONABLE_MIX: OfferMix = [
  { offer_code: 'refi', borrower_count: 55_871 },
  { offer_code: 'purchase', borrower_count: 41_220 },
  { offer_code: 'refi_plus_heloc', borrower_count: 18_904 },
  { offer_code: 'investor', borrower_count: 3_311 },
  { offer_code: 'cash_out', borrower_count: 2_164 },
  { offer_code: 'retention', borrower_count: 912 },
  { offer_code: 'heloc', borrower_count: 7 },
];
/** "Monitor for later": an offer decision that recommends no outreach. */
export const LIVE_MONITOR_COUNT = 4_620_000;
/** `offers_recommended`: every actionable code, never `nurture`. */
export const LIVE_OFFER_PATHS = LIVE_ACTIONABLE_MIX.reduce((sum, row) => sum + row.borrower_count, 0);

const ADDRESSABLE = 5_156_184;
const REFI_SCREEN = 74_775;
const TOP_TIER = 38_912;

/** The 'Any' preview of a live-shaped book: nurture-dominant, all eight codes. */
export const LIVE_SHAPED_HOME_PREVIEW: PortfolioPreview = {
  ...PORTFOLIO_PREVIEW,
  marketable_population: ADDRESSABLE,
  high_intent_leads: REFI_SCREEN,
  top_tier_opportunities: TOP_TIER,
  offers_recommended: LIVE_OFFER_PATHS,
  offer_mix: [{ offer_code: 'nurture', borrower_count: LIVE_MONITOR_COUNT }, ...LIVE_ACTIONABLE_MIX],
  trends: {
    marketable_population: trend(ADDRESSABLE, 1.2),
    high_intent_leads: trend(REFI_SCREEN, 4.6),
    top_tier_opportunities: trend(TOP_TIER, 1.5),
    offers_recommended: trend(LIVE_OFFER_PATHS, 3.1),
  },
};

/**
 * The tallest answer band the payload vocabulary can produce: the live-shaped
 * book above plus the "some figures could not be verified" line. The briefing
 * verifier (lib/portfolioStory.ts) compares a token's unsigned digits with
 * its source value, so a signed count is the payload shape that trips it;
 * the fold test needs that line rendered, not a plausible book.
 */
export const MAX_HOME_PREVIEW: PortfolioPreview = {
  ...LIVE_SHAPED_HOME_PREVIEW,
  high_intent_leads: -REFI_SCREEN,
};

/** Three WHY NOW triggers (SUMMARY_DELTA_MEASURES is three) with wide tokens. */
export const MAX_HOME_SUMMARY: HomeSummary = {
  ...HOME_SUMMARY,
  highlights: [
    { measure: 'high_opportunity', label: 'high-opportunity', display: '+12.5%', value_token: '+12.5%', current: TOP_TIER, baseline: 34_588, delta: 4_324, delta_pct: 12.5 },
    { measure: 'refi_economics_screen', label: 'refi candidates', display: '+22,250', value_token: '+22,250', current: REFI_SCREEN, baseline: 52_525, delta: 22_250, delta_pct: 42.4 },
    { measure: 'offers_available', label: 'offers available', display: '+111,190', value_token: '+111,190', current: LIVE_OFFER_PATHS + LIVE_MONITOR_COUNT, baseline: 4_631_199, delta: 111_190, delta_pct: 2.4 },
  ],
};

/**
 * A `POST /api/portfolio/preview` handler that answers Home's whole-book
 * (`marketing_eligibility: 'Any'`) request with `preview` and the approval
 * banner's contactable request with the default contactable preview, as the
 * registry's own handler does.
 */
export function homePreviewHandler(
  preview: PortfolioPreview,
): (request: FixtureRequest) => FixtureReply<PortfolioPreview> {
  return (request) => {
    const criteria = (request.body as { criteria?: { marketing_eligibility?: unknown } } | null)?.criteria;
    return json<PortfolioPreview>(
      criteria?.marketing_eligibility === 'Eligible only' ? CONTACTABLE_PORTFOLIO_PREVIEW : preview,
    );
  };
}
