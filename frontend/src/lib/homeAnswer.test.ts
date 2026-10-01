import { describe, expect, it } from 'vitest';
import type { HomeSummary, RateWindowWeek, TopBorrowerAnalyticsRow } from '../types';
import { parseFunnelStage, parsePortfolioCriteria } from '../routes/lead-queue.filters';
import {
  HOME_WHO_COUNT,
  borrowerPlace,
  borrowerQueueHref,
  homeTopBorrowers,
  leadQueueHref,
  rateMoveSinceVisit,
  whyNowTriggers,
} from './homeAnswer';

function row(rank: number, id = `B-${String(rank).padStart(13, '0')}`): TopBorrowerAnalyticsRow {
  return {
    borrower_id: id,
    display_name: `Owner ${rank}`,
    state: 'IL',
    city: 'Chicago',
    opportunity_score: 100 - rank,
    rate_spread_bps: 120,
    equity_pct: 40,
    recommended_offer: 'Refinance',
    rank_overall: rank,
  };
}

describe('homeTopBorrowers', () => {
  it('returns the first five of the ranking in rank order', () => {
    const rows = [7, 3, 1, 9, 2, 5, 4].map((rank) => row(rank));
    expect(homeTopBorrowers(rows).map((r) => r.rank_overall)).toEqual([1, 2, 3, 4, 5]);
    expect(HOME_WHO_COUNT).toBe(5);
  });

  it('never lists an id that is not a masked public id', () => {
    const rows = [row(1, 'CLIP-1234567890'), row(2), row(3, 'b-lowercase00000')];
    expect(homeTopBorrowers(rows).map((r) => r.borrower_id)).toEqual([row(2).borrower_id]);
  });

  it('is empty for an absent or malformed payload', () => {
    expect(homeTopBorrowers(undefined)).toEqual([]);
    expect(homeTopBorrowers(null)).toEqual([]);
    expect(homeTopBorrowers({} as unknown as TopBorrowerAnalyticsRow[])).toEqual([]);
  });
});

describe('Lead Queue links', () => {
  it('narrows the queue to one borrower through the borrower_ids URL filter', () => {
    expect(borrowerQueueHref('B-ABCDEFGHJKMNP')).toBe('/lead-queue?borrower_ids=B-ABCDEFGHJKMNP');
    expect(leadQueueHref()).toBe('/lead-queue');
    expect(leadQueueHref({ segment: 'itm', state: null })).toBe('/lead-queue?segment=itm');
  });

  it('writes a place as "City, ST" and falls back to the state', () => {
    expect(borrowerPlace({ city: 'Houston', state: 'TX' })).toBe('Houston, TX');
    expect(borrowerPlace({ city: '  ', state: 'TX' })).toBe('TX');
    expect(borrowerPlace({ city: null, state: 'TX' })).toBe('TX');
  });
});

const SUMMARY: HomeSummary = {
  status: 'delta',
  previous_visit_at: '2026-07-09T14:30:00+00:00',
  baseline_snapshot_at: '2026-07-09T06:00:00+00:00',
  headline: 'Since your last login: +1.5% high-opportunity, no change in refi candidates, +190 offers available.',
  phrasing_source: 'deterministic',
  phrasing_fallback_reason: null,
  highlights: [
    { measure: 'high_opportunity', label: 'high-opportunity', display: '+1.5%', value_token: '+1.5%', current: 10, baseline: 9, delta: 1, delta_pct: 1.5 },
    { measure: 'refi_economics_screen', label: 'refi candidates', display: 'no change', value_token: 'no change', current: 5, baseline: 5, delta: 0, delta_pct: 0 },
    { measure: 'offers_available', label: 'offers available', display: '+190', value_token: '+190', current: 7, baseline: 6, delta: 190, delta_pct: 3.1 },
    { measure: 'future_measure', label: 'future things', display: '+2', value_token: '+2', current: 2, baseline: 0, delta: 2, delta_pct: null },
  ],
  current: {},
  baseline: {},
  deltas: {},
  current_source: 'mip.semantics.portfolio_headline_metric_view',
  baseline_source: 'mip_app.kpi_snapshots',
};

describe('whyNowTriggers', () => {
  it('keeps every server token verbatim and in order', () => {
    expect(whyNowTriggers(SUMMARY).map((t) => t.display)).toEqual(['+1.5%', 'no change', '+190', '+2']);
  });

  it('maps each measure to the queue filter with the same predicate, or to none', () => {
    const hrefs = Object.fromEntries(whyNowTriggers(SUMMARY).map((t) => [t.highlight.measure, t.href]));
    expect(hrefs).toEqual({
      high_opportunity: '/lead-queue?funnel_stage=high_opportunity',
      refi_economics_screen: '/lead-queue?segment=itm',
      offers_available: null,
      future_measure: null,
    });
  });

  it('reads a zero movement as "no change in", and falls back to the server label', () => {
    const [, flat, , unknown] = whyNowTriggers(SUMMARY);
    expect(`${flat.display}${flat.joiner}${flat.noun}`).toBe(
      'no change in borrowers whose rate and equity pass the refinance screen',
    );
    expect(unknown.noun).toBe('future things');
  });

  it('reads a percent as a change in the population, a count as borrowers', () => {
    const [pct, , count] = whyNowTriggers(SUMMARY);
    // "+1.5% borrowers with ..." would read as a share of borrowers.
    expect(`${pct.display}${pct.joiner}${pct.noun}`).toBe(
      '+1.5% in borrowers with an opportunity score of 75+',
    );
    expect(`${count.display}${count.joiner}${count.noun}`).toBe('+190 borrowers with an offer decision');
    const negative = whyNowTriggers({
      ...SUMMARY,
      highlights: [{ ...SUMMARY.highlights[0], display: '-0.4%', value_token: '-0.4%', delta: -2, delta_pct: -0.4 }],
    })[0];
    expect(`${negative.display}${negative.joiner}${negative.noun}`).toBe(
      '-0.4% in borrowers with an opportunity score of 75+',
    );
  });

  it('is empty for an absent summary', () => {
    expect(whyNowTriggers(null)).toEqual([]);
    expect(whyNowTriggers({ ...SUMMARY, highlights: undefined } as unknown as HomeSummary)).toEqual([]);
  });
});

// flow-05: WHY NOW cites events, and every trigger opens the queue filter
// with the SAME predicate (the Lead Queue URL contract parses each back).
describe('WHY NOW event and offer triggers', () => {
  const EVENTS: HomeSummary = {
    ...SUMMARY,
    highlights: ['listed_for_sale', 'competitor_lien', 'offers_recommended', 'offers_available'].map((measure) => ({
      measure, label: measure, display: '+3', value_token: '+3', current: 3, baseline: 0, delta: 3, delta_pct: null,
    })),
  };

  it('links listings, competitor liens and primary offer paths to their exact queue filters', () => {
    const triggers = Object.fromEntries(whyNowTriggers(EVENTS).map((t) => [t.highlight.measure, t]));
    expect(triggers.listed_for_sale.href).toBe('/lead-queue?purchase_intent=Listed+for+sale');
    expect(triggers.listed_for_sale.noun).toBe('borrowers whose homes are listed for sale');
    expect(triggers.competitor_lien.href).toBe('/lead-queue?lender_relationship=Competitor+customer');
    expect(triggers.competitor_lien.noun).toBe('borrowers whose lien is held by a competitor');
    expect(triggers.offers_recommended.href).toBe('/lead-queue?funnel_stage=offer_recommended');
    expect(triggers.offers_recommended.noun).toBe('borrowers with a primary offer path');
    // Older payloads still render offers_available, with no link (no queue filter is that population).
    expect(triggers.offers_available.href).toBeNull();
  });

  it('every href survives the Lead Queue URL contract', () => {
    const params = (measure: string) => {
      const href = whyNowTriggers(EVENTS).find((t) => t.highlight.measure === measure)?.href ?? '';
      return new URL(href, 'https://mip.test').searchParams;
    };
    expect(parsePortfolioCriteria(params('listed_for_sale'), [])).toEqual({ purchase_intent: 'Listed for sale' });
    expect(parsePortfolioCriteria(params('competitor_lien'), [])).toEqual({ lender_relationship: 'Competitor customer' });
    expect(parseFunnelStage(params('offers_recommended').get('funnel_stage'))).toBe('offer_recommended');
  });
});

describe('rateMoveSinceVisit (flow-05)', () => {
  const week = (iso: string, pct: number, latest = false): RateWindowWeek => ({
    week: iso, market_rate_pct: pct, itm_count: 1, ...(latest ? { is_latest: true } : {}),
  });
  const WEEKS = [week('2026-06-29', 6.62), week('2026-07-06', 6.7), week('2026-07-13', 6.58), week('2026-09-28', 6.3, true)];

  it('compares the latest print with the week of the visit, in signed basis points', () => {
    expect(rateMoveSinceVisit(WEEKS, '2026-07-09T14:30:00+00:00')).toEqual({
      fromWeek: '2026-07-06', fromPct: 6.7, toWeek: '2026-09-28', toPct: 6.3, deltaBps: -40,
    });
    // The visit's UTC date decides the week: late on Sunday 12th in UTC-5 is Monday 13th UTC.
    expect(rateMoveSinceVisit(WEEKS, '2026-07-12T23:30:00-05:00')?.fromWeek).toBe('2026-07-13');
  });

  it('is null with no visit, no latest week, or a visit before the series', () => {
    expect(rateMoveSinceVisit(WEEKS, null)).toBeNull();
    expect(rateMoveSinceVisit(WEEKS, undefined)).toBeNull();
    expect(rateMoveSinceVisit(WEEKS, 'not a date')).toBeNull();
    expect(rateMoveSinceVisit(WEEKS.map((w) => ({ ...w, is_latest: false })), '2026-07-09T14:30:00Z')).toBeNull();
    expect(rateMoveSinceVisit(WEEKS, '2026-06-01T00:00:00Z')).toBeNull();
    expect(rateMoveSinceVisit(null, '2026-07-09T14:30:00Z')).toBeNull();
  });

  it('is null when the visit falls in the latest week or the par did not move', () => {
    expect(rateMoveSinceVisit(WEEKS, '2026-09-30T08:00:00Z')).toBeNull();
    expect(rateMoveSinceVisit([week('2026-07-06', 6.3), week('2026-09-28', 6.3, true)], '2026-07-09T00:00:00Z')).toBeNull();
    // A sub-half-basis-point drift rounds to no move.
    expect(rateMoveSinceVisit([week('2026-07-06', 6.3), week('2026-09-28', 6.304, true)], '2026-07-09T00:00:00Z')).toBeNull();
  });
});
