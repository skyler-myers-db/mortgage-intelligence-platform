import type { HomeSummary, HomeSummaryHighlight, RateWindowWeek, TopBorrowerAnalyticsRow } from '../types';
import { HIGH_OPPORTUNITY_SCORE_LABEL } from './opportunityScore';

/**
 * Pure helpers behind Home's answer band (2026-09-21 audit flow-05): WHO to
 * contact and WHY NOW. The band answers the page's own question with data
 * Home can read WITHOUT writing an audit row:
 *
 *   - WHO comes from `GET /api/analytics/economics` `top_borrowers`, the
 *     governed canonical ranking ("one top-10 across Genie, Lead Queue, and
 *     this panel": opportunity score >= 50 plus the contact-eligibility
 *     predicate). It is NOT `GET /api/leads`, which writes a VIEW_LEADS audit
 *     row per call; a landing page must never audit on render.
 *   - WHY NOW is the "since your last login" payload (`GET /api/home/summary`)
 *     already on Home. Its `display` tokens are server-minted; nothing here
 *     re-derives a number.
 *
 * Every link uses the Lead Queue's URL filter contract
 * (routes/lead-queue.filters.ts): `borrower_ids`, `segment`, `funnel_stage`.
 */

/** How many ranked borrowers the WHO column lists. */
export const HOME_WHO_COUNT = 5;

/** Same shape the Lead Queue accepts (`parseBorrowerIds` keeps `B-...`). */
const MASKED_BORROWER_ID_RE = /^B-[0-9A-Z]{13}$/;

/** The Lead Queue with the given URL filters (empty values dropped). */
export function leadQueueHref(params: Record<string, string | null | undefined> = {}): string {
  const search = new URLSearchParams();
  for (const [key, value] of Object.entries(params)) {
    if (value) search.set(key, value);
  }
  const qs = search.toString();
  return qs ? `/lead-queue?${qs}` : '/lead-queue';
}

/** The Lead Queue narrowed to one borrower (the row's own queue context). */
export function borrowerQueueHref(borrowerId: string): string {
  return leadQueueHref({ borrower_ids: borrowerId });
}

/**
 * The first `HOME_WHO_COUNT` rows of the governed ranking, in rank order.
 * Rows whose id is not a masked public id are dropped rather than rendered:
 * the band never shows anything that could be a raw identifier.
 */
export function homeTopBorrowers(
  rows: readonly TopBorrowerAnalyticsRow[] | null | undefined,
): TopBorrowerAnalyticsRow[] {
  if (!Array.isArray(rows)) return [];
  const source: readonly TopBorrowerAnalyticsRow[] = rows;
  return source
    .filter((row) => MASKED_BORROWER_ID_RE.test(row.borrower_id))
    .slice()
    .sort((a, b) => a.rank_overall - b.rank_overall)
    .slice(0, HOME_WHO_COUNT);
}

/** "Chicago, IL" — or the state alone when the row carries no city. */
export function borrowerPlace(row: Pick<TopBorrowerAnalyticsRow, 'city' | 'state'>): string {
  const city = row.city?.trim();
  return city ? `${city}, ${row.state}` : row.state;
}

interface TriggerCopy {
  /** Plain-language noun phrase that follows the server token. */
  noun: string;
  /** Lead Queue filter that opens this population, or null when none is exact. */
  href: string | null;
}

/**
 * Plain lender language for each summary measure, and the queue filter whose
 * predicate is the SAME population:
 *
 *   listed_for_sale       -> `purchase_intent=Listed for sale`: the same
 *                            `listed_for_sale` indicator (flow-05 events)
 *   competitor_lien       -> `lender_relationship=Competitor customer`: the
 *                            same `is_competitor_lien` indicator
 *   refi_economics_screen -> `segment=itm`: `in_the_money` is exactly what puts
 *                            `itm` in segment_codes (the approval banner's link)
 *   high_opportunity      -> `funnel_stage=high_opportunity`: score >= 75
 *   offers_recommended    -> `funnel_stage=offer_recommended`: a non-null,
 *                            non-nurture offer, the "Primary offer paths" KPI
 *   marketable_population -> the whole queue (first visit: "your book today")
 *   offers_available      -> none (kept for older payloads; the server now
 *                            sends offers_recommended). `offer_available`
 *                            counts every borrower with ANY offer decision,
 *                            "Monitor for later" included, so no queue
 *                            filter is that population.
 */
const TRIGGER_COPY: Record<string, TriggerCopy> = {
  listed_for_sale: {
    noun: 'borrowers whose homes are listed for sale',
    href: leadQueueHref({ purchase_intent: 'Listed for sale' }),
  },
  competitor_lien: {
    noun: 'borrowers whose lien is held by a competitor',
    href: leadQueueHref({ lender_relationship: 'Competitor customer' }),
  },
  offers_recommended: {
    noun: 'borrowers with a primary offer path',
    href: leadQueueHref({ funnel_stage: 'offer_recommended' }),
  },
  refi_economics_screen: {
    noun: 'borrowers whose rate and equity pass the refinance screen',
    href: leadQueueHref({ segment: 'itm' }),
  },
  high_opportunity: {
    noun: `borrowers with an opportunity score of ${HIGH_OPPORTUNITY_SCORE_LABEL}`,
    href: leadQueueHref({ funnel_stage: 'high_opportunity' }),
  },
  offers_available: {
    noun: 'borrowers with an offer decision',
    href: null,
  },
  marketable_population: {
    noun: 'borrowers in your addressable book',
    href: leadQueueHref(),
  },
};

export interface WhyNowTrigger {
  highlight: HomeSummaryHighlight;
  /** The server-minted token, verbatim ("+2,250", "+1.5%", "no change"). */
  display: string;
  /**
   * Joins the token to the noun. A count reads as borrowers ("+2,250
   * borrowers whose ..."); "no change" and a percent read as a change IN the
   * population ("+1.5% in borrowers with ..."), never as a share of it.
   */
  joiner: string;
  noun: string;
  href: string | null;
}

/**
 * One trigger per summary highlight, in the server's order. The token is the
 * backend's exact `display`; an unknown measure falls back to the backend's
 * own label so a new measure still renders honestly.
 */
export function whyNowTriggers(summary: HomeSummary | null | undefined): WhyNowTrigger[] {
  if (!summary || !Array.isArray(summary.highlights)) return [];
  return summary.highlights.map((highlight) => {
    const copy = TRIGGER_COPY[highlight.measure];
    return {
      highlight,
      display: highlight.display,
      joiner: highlight.delta === 0 || highlight.display.trim().endsWith('%') ? ' in ' : ' ',
      noun: copy?.noun ?? highlight.label,
      href: copy ? copy.href : null,
    };
  });
}

/** The 30-year par move between the week of the last visit and the latest week (flow-05). */
export interface RateMoveSinceVisit {
  /** Week-starting Monday (YYYY-MM-DD) of the print that was current at the visit. */
  fromWeek: string;
  fromPct: number;
  /** The latest week (`is_latest`). */
  toWeek: string;
  toPct: number;
  /** Signed basis points: the one client-side subtraction, of two server prints. */
  deltaBps: number;
}

/** The visit's UTC calendar date, or null when it does not parse. */
function utcDate(instant: string): string | null {
  const at = Date.parse(instant);
  return Number.isFinite(at) ? new Date(at).toISOString().slice(0, 10) : null;
}

/**
 * The FRED MORTGAGE30US move since the last visit, from the rate window's own
 * weekly prints: the latest week against the latest week whose Monday is on
 * or before the visit's UTC date. Null with no visit, no latest week, a visit
 * that predates the series, the same week on both sides, or no move.
 */
export function rateMoveSinceVisit(
  weeks: readonly RateWindowWeek[] | null | undefined,
  previousVisitAt: string | null | undefined,
): RateMoveSinceVisit | null {
  if (!previousVisitAt || !Array.isArray(weeks)) return null;
  const visitDate = utcDate(previousVisitAt);
  const latest = weeks.find((week) => week.is_latest === true);
  if (!visitDate || !latest) return null;
  const from = weeks
    .filter((week) => week.week <= visitDate)
    .reduce<RateWindowWeek | null>((best, week) => (best === null || week.week > best.week ? week : best), null);
  if (!from || from.week === latest.week) return null;
  const deltaBps = Math.round((latest.market_rate_pct - from.market_rate_pct) * 100);
  if (deltaBps === 0) return null;
  return { fromWeek: from.week, fromPct: from.market_rate_pct, toWeek: latest.week, toPct: latest.market_rate_pct, deltaBps };
}
