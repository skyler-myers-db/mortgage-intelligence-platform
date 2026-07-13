/** @vitest-environment happy-dom */

import { act } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router-dom';
import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the design-system CSS text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import {
  SCORE_BAND_HIGH_MIN,
  SCORE_BAND_MED_MIN,
} from '../lib/opportunityScore';
import type { EquitySpreadOverview, EquitySpreadPointsResponse } from '../types';
import {
  EquitySpreadBinsView,
  EquitySpreadPointsView,
  groupExactCoordinatePoints,
} from './analytics.equity-scatter';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

declare const process: { cwd(): string };

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

const collisionDrilldown: EquitySpreadPointsResponse = {
  ...drilldown,
  points: [
    {
      ...drilldown.points[0],
      borrower_id: 'B-0000000000002',
      display_name: 'Owner 2',
      opportunity_score: SCORE_BAND_HIGH_MIN,
      score_band: 'high',
    },
    {
      ...drilldown.points[0],
      borrower_id: 'B-0000000000001',
      display_name: 'Owner 1',
      opportunity_score: SCORE_BAND_MED_MIN,
      score_band: 'med',
    },
    {
      ...drilldown.points[0],
      borrower_id: 'B-0000000000003',
      display_name: 'Owner 3',
      equity_pct: 43,
      rate_spread_bps: 90,
    },
  ],
  total_matching: 3,
  showing: 3,
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
    expect(html).toContain('Colors show individual opportunity scores.');
    expect(html).toContain('Exact-coordinate cluster count');
    expect(html).toContain('analytics-scatter__dot--band score--med');
  });

  it('groups exact collisions and orders every masked borrower link deterministically', () => {
    const groups = groupExactCoordinatePoints(collisionDrilldown.points);
    expect(groups).toHaveLength(2);
    expect(groups[0].points.map((point) => point.borrower_id)).toEqual([
      'B-0000000000001',
      'B-0000000000002',
    ]);

    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView payload={collisionDrilldown} />
      </MemoryRouter>,
    );
    expect(html).toContain('analytics-scatter__cluster-marker score--high');
    expect(html).toContain('aria-expanded="false"');
    expect(html).toContain('2 borrowers at 42% equity and 88 bps spread');
    expect(html).not.toContain('/borrower-360/B-0000000000001');
    expect(html).toContain('/borrower-360/B-0000000000003');
  });

  it('progressively reveals dense cluster links instead of rendering them all while closed', () => {
    const dense = {
      ...drilldown,
      points: Array.from({ length: 75 }, (_, index) => ({
        ...drilldown.points[0],
        borrower_id: `B-${String(index).padStart(13, '0')}`,
      })),
      showing: 75,
      total_matching: 75,
    };
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      act(() => {
        root.render(
          <MemoryRouter>
            <EquitySpreadPointsView payload={dense} />
          </MemoryRouter>,
        );
      });
      expect(container.querySelectorAll('.analytics-scatter__cluster-link')).toHaveLength(0);
      act(() => container.querySelector<HTMLButtonElement>('.analytics-scatter__cluster-marker')!.click());
      expect(container.querySelectorAll('.analytics-scatter__cluster-link')).toHaveLength(50);
      const more = container.querySelector<HTMLButtonElement>('.analytics-scatter__cluster-more');
      expect(more?.textContent).toContain('Show 25 more');
      act(() => more!.click());
      expect(container.querySelectorAll('.analytics-scatter__cluster-link')).toHaveLength(75);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });

  it('distinguishes plotted, returned, and total-matching populations at the client cap', () => {
    const capped = {
      ...drilldown,
      points: Array.from({ length: 1_201 }, (_, index) => ({
        ...drilldown.points[0],
        borrower_id: `B-${String(index).padStart(13, '0')}`,
        equity_pct: 40 + (index % 5),
        rate_spread_bps: 75 + (index % 20),
      })),
      showing: 3_000,
      total_matching: 3_000,
    };
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView payload={capped} />
      </MemoryRouter>,
    );

    expect(html).toContain('Plotting 1.2K of 3K returned borrowers');
    expect(html).toContain('(3K total matching this window)');
    expect(html).not.toContain('Showing 3K of 3K');
    expect(html).toContain('every plotted borrower at that coordinate');
  });

  it('opens a cluster from its native button and Escape closes it back to the marker', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      act(() => {
        root.render(
          <MemoryRouter>
            <EquitySpreadPointsView payload={collisionDrilldown} />
          </MemoryRouter>,
        );
      });
      const marker = container.querySelector<HTMLButtonElement>('.analytics-scatter__cluster-marker');
      const panel = container.querySelector<HTMLElement>('.analytics-scatter__cluster-panel');
      expect(marker).not.toBeNull();
      expect(panel).not.toBeNull();
      expect(panel!.hidden).toBe(true);

      act(() => marker!.click());
      expect(marker!.getAttribute('aria-expanded')).toBe('true');
      expect(panel!.hidden).toBe(false);
      expect(panel!.querySelectorAll('a')).toHaveLength(2);

      act(() => {
        panel!.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });
      expect(marker!.getAttribute('aria-expanded')).toBe('false');
      expect(panel!.hidden).toBe(true);
      expect(document.activeElement).toBe(marker);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });

  it('moves keyboard focus across borrower markers with arrow, Home, and End keys', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      act(() => {
        root.render(
          <MemoryRouter>
            <EquitySpreadPointsView payload={collisionDrilldown} />
          </MemoryRouter>,
        );
      });
      const markers = [...container.querySelectorAll<HTMLElement>('[data-scatter-marker]')];
      expect(markers).toHaveLength(2);
      markers[0].focus();
      act(() => markers[0].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowRight', bubbles: true,
      })));
      expect(document.activeElement).toBe(markers[1]);
      act(() => markers[1].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'Home', bubbles: true,
      })));
      expect(document.activeElement).toBe(markers[0]);
      act(() => markers[0].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'End', bubbles: true,
      })));
      expect(document.activeElement).toBe(markers[1]);
      act(() => markers[1].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowLeft', bubbles: true,
      })));
      expect(document.activeElement).toBe(markers[0]);
      act(() => markers[0].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowUp', bubbles: true,
      })));
      expect(document.activeElement).toBe(markers[1]);
      act(() => markers[1].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowDown', bubbles: true,
      })));
      expect(document.activeElement).toBe(markers[0]);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });

  it('keeps scatter loading height stable while reducing the chart height on narrow containers', () => {
    const css = [
      join(process.cwd(), 'src', 'design-system', 'components.css'),
      join(process.cwd(), 'src', 'routes', 'analytics.scatter.css'),
    ].map((path) => readFileSync(path, 'utf8')).join('\n');

    expect(css).toMatch(/\.analytics-chart-panel--scatter\s*\{[^}]*min-height:/s);
    expect(css).toMatch(
      /@container main \(max-width: 640px\)[\s\S]*?\.analytics-chart-panel--scatter\s*\{[^}]*min-height:\s*calc\(\(var\(--sp-16\) \* 5\) \+ var\(--sp-16\)\)/,
    );
    expect(css).toMatch(
      /@container main \(max-width: 640px\)[\s\S]*?\.analytics-scatter-wrap\s*\{[^}]*--analytics-chart-h:\s*calc\(var\(--sp-16\) \* 5\)/,
    );
  });
});
