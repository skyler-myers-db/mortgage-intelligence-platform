import { describe, expect, it } from 'vitest';
import type { HomeSummary, TopBorrowerAnalyticsRow } from '../types';
import {
  HOME_WHO_COUNT,
  borrowerPlace,
  borrowerQueueHref,
  homeTopBorrowers,
  leadQueueHref,
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

  it('is empty for an absent summary', () => {
    expect(whyNowTriggers(null)).toEqual([]);
    expect(whyNowTriggers({ ...SUMMARY, highlights: undefined } as unknown as HomeSummary)).toEqual([]);
  });
});
