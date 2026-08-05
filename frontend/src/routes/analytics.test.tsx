import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
import {
  DailyEvidenceLineChart,
  EquitySpreadBinsView,
  EquitySpreadPointsView,
  LineChart,
  binCellRect,
  binDensityAlpha,
  binZoomViewport,
  buildDailyEvidenceTotals,
  leadQueueHrefForFunnelStage,
  leadQueueHref,
  normalizeAnalyticsSegmentCodes,
  scatterPosition,
  segmentIntelligenceHref,
  zoomScatterLayout,
} from './analytics';
import { MAX_SCATTER_POINTS, signalLabel } from './analytics.lib';
import type { EquitySpreadOverview, EquitySpreadPoint, EquitySpreadPointsResponse } from '../types';

const OVERVIEW_FIXTURE: EquitySpreadOverview = {
  bins: [
    { equity_bin_pct: 40, spread_bin_bps: 75, borrower_count: 1_800, mean_opportunity_score: 86, in_the_money_borrowers: 1_200 },
    { equity_bin_pct: 100, spread_bin_bps: 400, borrower_count: 3, mean_opportunity_score: 60, in_the_money_borrowers: 1 },
  ],
  total_borrowers: 1_803,
  equity_bin_pct: 5,
  spread_bin_bps: 25,
  equity_domain_min: 0,
  equity_domain_max: 100,
  spread_domain_min: -100,
  spread_domain_max: 400,
  source_table: 'mip.gold.equity_spread_points',
  refreshed_at: '2026-07-10 06:00:00',
};

function pointFixture(overrides: Partial<EquitySpreadPoint> = {}): EquitySpreadPoint {
  return {
    borrower_id: 'B-0000000000025',
    display_name: 'Owner 25',
    segment: 'Prime Refi Candidates',
    state: 'IL',
    equity_pct: 42,
    rate_spread_bps: 88,
    opportunity_score: 90,
    coordinate_total: 1,
    score_band: 'high',
    in_the_money: true,
    ...overrides,
  };
}

function pointsPayload(overrides: Partial<EquitySpreadPointsResponse> = {}): EquitySpreadPointsResponse {
  return {
    points: [pointFixture()],
    total_matching: 1,
    showing: 1,
    point_cap: 5000,
    truncated: false,
    viewport: { equity_min: 40, equity_max: 44, spread_min: 75, spread_max: 99 },
    source_table: 'mip.gold.equity_spread_points',
    refreshed_at: '2026-07-10 06:00:00',
    ...overrides,
  };
}

describe('signalLabel casing', () => {
  it('returns curated labels for known signal types', () => {
    expect(signalLabel('market_trend')).toBe('Market trend');
    expect(signalLabel('equity')).toBe('Equity');
  });

  it('sentence-cases unknown signal types instead of leaving them lowercase', () => {
    expect(signalLabel('product_type')).toBe('Product type');
    expect(signalLabel('origination_channel')).toBe('Origination channel');
  });
});

describe('analytics drilldown links', () => {
  it('routes every funnel stage through the exact backend funnel_stage contract', () => {
    expect(leadQueueHrefForFunnelStage({ stage: 'Addressable', stage_order: 1 })).toBe('/lead-queue?funnel_stage=addressable');
    expect(leadQueueHrefForFunnelStage({ stage: 'In the Money', stage_order: 2 })).toBe('/lead-queue?funnel_stage=in_the_money');
    expect(leadQueueHrefForFunnelStage({ stage: 'High Opportunity', stage_order: 3 })).toBe('/lead-queue?funnel_stage=high_opportunity');
    expect(leadQueueHrefForFunnelStage({ stage: 'Offer Recommended', stage_order: 4 })).toBe('/lead-queue?funnel_stage=offer_recommended');
    expect(leadQueueHrefForFunnelStage({ stage: 'Approved', stage_order: 5 })).toBe('/lead-queue?funnel_stage=approved');
    expect(leadQueueHrefForFunnelStage({ stage: 'Actioned', stage_order: 6 })).toBe('/lead-queue?funnel_stage=actioned');
  });

  it('preserves lender overlay filters in funnel drilldowns', () => {
    const href = leadQueueHrefForFunnelStage(
      { stage: 'Addressable', stage_order: 1 },
      {
        lender_relationship: 'Competitor customer',
        target_lender_ref: 'Competitor B',
      },
    );

    expect(href).toContain('funnel_stage=addressable');
    expect(href).toContain('lender_relationship=Competitor+customer');
    expect(href).toContain('target_lender_ref=Competitor+B');
  });

  it('preserves lender overlay filters when opening the segment map', () => {
    expect(segmentIntelligenceHref({
      lender_relationship: 'Competitor customer',
      target_lender_ref: 'Competitor B',
    })).toBe('/segment-intelligence?lender_relationship=Competitor+customer&target_lender_ref=Competitor+B');
  });

  it('builds one-segment lead queue drilldowns with segment and no match mode', () => {
    expect(leadQueueHref({ state: 'IL', segment: 'itm' })).toBe(
      '/lead-queue?state=IL&segment=itm',
    );
    expect(leadQueueHref({ state: 'IL', segment_codes: 'itm,equity', segment_mode: 'all' })).toBe(
      '/lead-queue?state=IL&segment_codes=itm%2Cequity&segment_mode=all',
    );
  });

  it('normalizes mixed-case analytics segment URL state before querying', () => {
    expect(normalizeAnalyticsSegmentCodes(['ITM', 'Equity', 'itm', 'drop_table'])).toEqual([
      'itm',
      'equity',
    ]);
  });
});

describe('analytics chart readability', () => {
  it('renders numeric x and y tick labels for line charts', () => {
    const html = renderToStaticMarkup(
      <LineChart
        rows={[
          { score_bucket: 10, borrower_count: 100 },
          { score_bucket: 20, borrower_count: 2_000 },
        ]}
        x={(row) => ('score_bucket' in row ? row.score_bucket : row.spread_bucket_bps)}
        y={(row) => row.borrower_count}
        xLabel="Opportunity score"
        yLabel="Borrowers"
      />,
    );

    expect(html).toContain('Opportunity score');
    expect(html).toContain('Borrowers');
    expect(html).toContain('10');
    expect(html).toContain('20');
    expect(html).toContain('2K');
    expect(html).not.toContain('Borrowers scale');
    expect(html).toContain('analytics-chart__grid');
    expect(html.match(/analytics-chart__tick--x/g)?.length ?? 0).toBeGreaterThanOrEqual(5);
    expect(html.match(/analytics-chart__tick--y/g)?.length ?? 0).toBeGreaterThanOrEqual(5);

    const points = html.match(/points="([^"]+)"/)?.[1] ?? '';
    const ys = points.split(' ').map((point) => Number(point.split(',')[1]));
    expect(ys.length).toBe(2);
    expect(ys.every((value) => Number.isFinite(value) && value >= 0 && value <= 100)).toBe(true);
  });

  it('renders the scatter overview as density-bin cells, never borrower rows', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadBinsView overview={OVERVIEW_FIXTURE} onZoom={() => {}} />
      </MemoryRouter>,
    );

    expect(html).toContain('Equity percent');
    expect(html).toContain('Rate spread bps');
    expect(html.match(/analytics-scatter__bin/g)?.length ?? 0).toBeGreaterThanOrEqual(2);
    // Bin cells are colored by the canonical S1 band classes.
    expect(html).toContain('score--high');
    expect(html).toContain('score--low');
    // Honest overview copy: total + how to reach real borrowers.
    expect(html).toContain('1.8K borrowers, binned server-side');
    // The overview must not ship borrower identifiers.
    expect(html).not.toContain('/borrower-360/');
    expect(html).not.toContain('B-0000000000025');
  });

  it('renders zoomed real points with honest returned-versus-matching copy and Borrower 360 links', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView
          payload={pointsPayload({
            points: [
              pointFixture(),
              pointFixture({
                borrower_id: 'B-0000000000075',
                equity_pct: 43,
                score_band: 'low',
                opportunity_score: 40,
              }),
            ],
            total_matching: 970,
            showing: 2,
            truncated: true,
          })}
        />
      </MemoryRouter>,
    );

    expect(html).toContain('Plotting 2 of 2 returned borrowers');
    expect(html).toContain('(970 total matching this window)');
    expect(html).toContain('server cap 5K');
    expect(html).toContain('/borrower-360/B-0000000000025');
    expect(html).toContain('/borrower-360/B-0000000000075');
    // Individual dots carry the canonical band classes from the payload.
    expect(html).toContain('analytics-scatter__dot--band score--high');
    expect(html).toContain('analytics-scatter__dot--band score--low');
  });

  it('falls back to the pinned scoreBand constants when a point payload lacks score_band', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView
          payload={pointsPayload({ points: [pointFixture({ score_band: null, opportunity_score: 90 })] })}
        />
      </MemoryRouter>,
    );
    expect(html).toContain('score--high');
  });

  it('says when the DOM plot is a top slice of the honest server page', () => {
    const many = Array.from({ length: MAX_SCATTER_POINTS + 50 }, (_, idx) =>
      pointFixture({
        borrower_id: `B-${String(idx).padStart(13, '0')}`,
        coordinate_total: MAX_SCATTER_POINTS + 50,
      }));
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView
          payload={pointsPayload({ points: many, showing: many.length, total_matching: many.length })}
        />
      </MemoryRouter>,
    );
    expect(html).toContain(`the plotted points are the top ${(MAX_SCATTER_POINTS / 1000).toFixed(1)}K by opportunity score`);
    expect(html).toContain(`${many.length} borrowers at 42% equity and 88 bps spread`);
    expect(html).not.toContain('/borrower-360/B-0000000001249');
  });

  it('renders evidence daily dates as dates instead of mangled integers', () => {
    const rows = buildDailyEvidenceTotals([
      { event_date: '2026-05-18', signal_type: 'rate_spread', event_count: 3 },
      { event_date: '2026-05-18', signal_type: 'equity', event_count: 7 },
      { event_date: '2026-05-20', signal_type: 'rate_spread', event_count: 5 },
    ]);

    expect(rows).toEqual([
      { event_date: '2026-05-18', event_count: 10 },
      { event_date: '2026-05-19', event_count: 0 },
      { event_date: '2026-05-20', event_count: 5 },
    ]);

    const html = renderToStaticMarkup(<DailyEvidenceLineChart rows={rows} />);

    expect(html).toContain('May 18');
    expect(html).toContain('May 19');
    expect(html).toContain('May 20');
    expect(html).toContain('Event date');
    expect(html).toContain('Events');
    expect(html).not.toContain('Recent days');
    expect(html).not.toContain('518');
    expect(html).not.toContain('520');
  });

});

describe('equity-spread scatter geometry', () => {
  it('gives boundary bins full-size cells inside the extended plot range', () => {
    const edge = OVERVIEW_FIXTURE.bins[1]; // equity 100, spread 400 — inclusive domain max
    const rect = binCellRect(edge, OVERVIEW_FIXTURE);
    expect(rect.wPct).toBeCloseTo((5 / 105) * 100, 5);
    expect(rect.hPct).toBeCloseTo((25 / 525) * 100, 5);
    expect(rect.xPct).toBeCloseTo((100 / 105) * 100, 5);
    expect(rect.yPct).toBeCloseTo(0, 5); // top row of the plot
  });

  it('zooms a bin to its integer-inclusive disjoint window, clamped to the domain', () => {
    expect(binZoomViewport(OVERVIEW_FIXTURE.bins[0], OVERVIEW_FIXTURE)).toEqual({
      equity_min: 40,
      equity_max: 44,
      spread_min: 75,
      spread_max: 99,
    });
    expect(binZoomViewport(OVERVIEW_FIXTURE.bins[1], OVERVIEW_FIXTURE)).toEqual({
      equity_min: 100,
      equity_max: 100,
      spread_min: 400,
      spread_max: 400,
    });
  });

  it('positions zoom dots inside the viewport box', () => {
    const layout = zoomScatterLayout({ equity_min: 40, equity_max: 44, spread_min: 75, spread_max: 99 });
    const inside = scatterPosition(42, 88, layout);
    expect(inside.xPct).toBeGreaterThan(0);
    expect(inside.xPct).toBeLessThan(100);
    expect(inside.yPct).toBeGreaterThan(0);
    expect(inside.yPct).toBeLessThan(100);
    const corner = scatterPosition(40, 75, layout);
    expect(corner.xPct).toBe(0);
    expect(corner.yPct).toBe(100);
  });

  it('scales density opacity monotonically and keeps sparse bins visible', () => {
    const sparse = binDensityAlpha(1, 1_800);
    const dense = binDensityAlpha(1_800, 1_800);
    expect(sparse).toBeGreaterThanOrEqual(0.18);
    expect(dense).toBe(1);
    expect(binDensityAlpha(900, 1_800)).toBeGreaterThan(sparse);
    expect(binDensityAlpha(900, 1_800)).toBeLessThan(dense);
    expect(binDensityAlpha(0, 0)).toBe(0.18);
  });
});
