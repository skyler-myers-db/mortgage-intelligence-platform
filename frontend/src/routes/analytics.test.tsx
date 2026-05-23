import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import {
  DailyEvidenceLineChart,
  LineChart,
  ScatterPlot,
  buildDailyEvidenceTotals,
  compactScatterRows,
  leadQueueHrefForFunnelStage,
  segmentIntelligenceHref,
} from './analytics';

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

  it('renders numeric x and y tick labels for scatter plots', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <ScatterPlot
          rows={[
            { borrower_id: 'B-25', display_name: 'Borrower 25', segment: 'itm', state: 'IL', equity_pct: 25, rate_spread_bps: 100, opportunity_score: 80 },
            { borrower_id: 'B-75', display_name: 'Borrower 75', segment: 'equity', state: 'TX', equity_pct: 75, rate_spread_bps: 250, opportunity_score: 70 },
          ]}
        />
      </MemoryRouter>,
    );

    expect(html).toContain('Equity percent');
    expect(html).toContain('Rate spread bps');
    expect(html).toContain('0');
    expect(html).toContain('100');
    expect(html).toContain('400');
    expect(html).not.toContain('Rate spread scale');
    expect(html).toContain('analytics-chart__grid');
    expect(html.match(/analytics-chart__tick--x/g)?.length ?? 0).toBeGreaterThanOrEqual(5);
    expect(html.match(/analytics-chart__tick--y/g)?.length ?? 0).toBeGreaterThanOrEqual(5);
    expect(html).toContain('/borrower-360/B-25');
    expect(html).toContain('/borrower-360/B-75');
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

  it('compacts dense scatter duplicates into clickable representative borrower points', () => {
    const rows = Array.from({ length: 1_300 }, (_, idx) => ({
      borrower_id: `B-${idx}`,
      display_name: `Borrower ${idx}`,
      segment: 'Home Equity Candidate',
      state: 'IL',
      equity_pct: 100,
      rate_spread_bps: idx < 1_000 ? 0 : idx,
      opportunity_score: idx,
    }));
    const compacted = compactScatterRows(rows, 1_200);

    expect(compacted.length).toBeLessThan(rows.length);
    expect(compacted.length).toBeLessThanOrEqual(1_200);
    expect(compacted.some((row) => row.borrower_id === 'B-999')).toBe(true);
  });
});
