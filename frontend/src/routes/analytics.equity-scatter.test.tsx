import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
import {
  SCORE_BAND_HIGH_MIN,
  SCORE_BAND_MED_MIN,
} from '../lib/opportunityScore';
import type { EquitySpreadOverview, EquitySpreadPointsResponse } from '../types';
import { EquitySpreadBinsView, EquitySpreadPointsView } from './analytics.equity-scatter';

const overview: EquitySpreadOverview = {
  bins: [
    {
      equity_bin_pct: 40,
      spread_bin_bps: 75,
      borrower_count: 12,
      mean_opportunity_score: SCORE_BAND_HIGH_MIN,
      in_the_money_borrowers: 8,
    },
  ],
  total_borrowers: 12,
  equity_bin_pct: 5,
  spread_bin_bps: 25,
  equity_domain_min: 0,
  equity_domain_max: 100,
  spread_domain_min: -100,
  spread_domain_max: 400,
  source_table: 'mip.gold.equity_spread_points',
};

const drilldown: EquitySpreadPointsResponse = {
  points: [
    {
      borrower_id: 'B-0000000000025',
      display_name: 'Owner 25',
      segment: 'Prime Refi Candidates',
      state: 'IL',
      equity_pct: 42,
      rate_spread_bps: 88,
      opportunity_score: SCORE_BAND_MED_MIN,
      score_band: 'med',
    },
  ],
  total_matching: 1,
  showing: 1,
  point_cap: 5_000,
  truncated: false,
  viewport: { equity_min: 40, equity_max: 44, spread_min: 75, spread_max: 99 },
  source_table: 'mip.gold.equity_spread_points',
};

describe('Equity versus rate spread score-band legend', () => {
  it('labels canonical score ranges and identifies overview colors as cell means', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadBinsView overview={overview} onZoom={() => undefined} />
      </MemoryRouter>,
    );

    expect(html).toContain('aria-label="Opportunity score color legend for overview cell means"');
    expect(html).toContain(`High ${SCORE_BAND_HIGH_MIN}-100`);
    expect(html).toContain(
      `Medium ${SCORE_BAND_MED_MIN}-${SCORE_BAND_HIGH_MIN - 1}`,
    );
    expect(html).toContain(`Low 0-${SCORE_BAND_MED_MIN - 1}`);
    expect(html).toContain('Color metric: mean opportunity score per overview cell.');
  });

  it('identifies drilldown colors as individual borrower scores', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView payload={drilldown} />
      </MemoryRouter>,
    );

    expect(html).toContain(
      'aria-label="Opportunity score color legend for borrower drilldown points"',
    );
    expect(html).toContain('Color metric: individual borrower opportunity score.');
    expect(html).toContain('analytics-scatter__dot--band score--med');
  });
});
