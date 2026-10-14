/** @vitest-environment happy-dom */

import { act, type ReactElement } from 'react';
import { createRoot } from 'react-dom/client';
import { renderToStaticMarkup } from 'react-dom/server';
import { MemoryRouter } from 'react-router';
import { describe, expect, it } from 'vitest';
// @ts-expect-error Frontend app types intentionally exclude Node globals; this
// unit test reads the design-system CSS text under Vitest only.
import { readFileSync } from 'node:fs';
// @ts-expect-error see node:fs note above.
import { join } from 'node:path';
import { designCss } from '../test/designCss';
import { escapeLayerCount, pushEscapeLayer } from '../lib/escapeStack';
import {
  SCORE_BAND_HIGH_MIN,
  SCORE_BAND_MED_MIN,
} from '../lib/opportunityScore';
import type { AnalyticsThresholds, EquitySpreadOverview, EquitySpreadPointsResponse } from '../types';
import {
  EquitySpreadBinsView,
  EquitySpreadPointsView,
  groupExactCoordinatePoints,
} from './analytics.equity-scatter';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

declare const process: { cwd(): string };

/** The refresh's governed In the Money thresholds (the economics payload's `thresholds`). */
const THRESHOLDS: AnalyticsThresholds = { min_spread_bps: 75, min_equity_pct: 20, reason: null };

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
      coordinate_total: 1,
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
      coordinate_total: 2,
      score_band: 'high',
    },
    {
      ...drilldown.points[0],
      borrower_id: 'B-0000000000001',
      display_name: 'Owner 1',
      opportunity_score: SCORE_BAND_MED_MIN,
      coordinate_total: 2,
      score_band: 'med',
    },
    {
      ...drilldown.points[0],
      borrower_id: 'B-0000000000003',
      display_name: 'Owner 3',
      equity_pct: 43,
      rate_spread_bps: 90,
      coordinate_total: 1,
    },
  ],
  total_matching: 3,
  showing: 3,
};

describe('Equity versus rate spread score-band legend', () => {
  it('labels canonical score ranges and identifies overview colors as cell means', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadBinsView thresholds={THRESHOLDS} overview={overview} onZoom={() => undefined} />
      </MemoryRouter>,
    );

    expect(html).toContain('aria-label="Opportunity score color legend for overview cell means"');
    expect(html).toContain(`High ${SCORE_BAND_HIGH_MIN}-100`);
    expect(html).toContain(
      `Medium ${SCORE_BAND_MED_MIN}-${SCORE_BAND_HIGH_MIN - 1}`,
    );
    expect(html).toContain(`Low 0-${SCORE_BAND_MED_MIN - 1}`);
    expect(html).toContain('Color metric: mean opportunity score per overview cell.');
    expect(html).toContain('Opacity: lower to higher borrower density');
    expect(html).toContain('analytics-scatter-legend__density-swatch is-lower');
    expect(html).toContain('analytics-scatter-legend__density-swatch is-higher');
  });

  it('identifies drilldown colors as individual borrower scores', () => {
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView thresholds={THRESHOLDS} payload={drilldown} />
      </MemoryRouter>,
    );

    expect(html).toContain(
      'aria-label="Opportunity score color legend for borrower drilldown points"',
    );
    expect(html).toContain('Colors show individual opportunity scores.');
    expect(html).toContain('Exact-coordinate cluster count');
    expect(html).toContain('analytics-scatter__dot--band score--med');
  });

  it('groups exact collisions and preserves the governed server ranking', () => {
    const groups = groupExactCoordinatePoints(collisionDrilldown.points);
    expect(groups).toHaveLength(2);
    expect(groups[0].points.map((point) => point.borrower_id)).toEqual([
      'B-0000000000002',
      'B-0000000000001',
    ]);

    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView thresholds={THRESHOLDS} payload={collisionDrilldown} />
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
        coordinate_total: 75,
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
            <EquitySpreadPointsView thresholds={THRESHOLDS} payload={dense} />
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
      points: Array.from({ length: 5_000 }, (_, index) => ({
        ...drilldown.points[0],
        borrower_id: `B-${String(index).padStart(13, '0')}`,
        coordinate_total: 7_000,
      })),
      showing: 5_000,
      total_matching: 7_000,
      truncated: true,
    };
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView thresholds={THRESHOLDS} payload={capped} />
      </MemoryRouter>,
    );

    expect(html).toContain('Plotting 1.2K of 5K returned borrowers');
    expect(html).toContain('(7K total matching this window) (server cap 5K)');
    expect(html).not.toContain('Showing 7K of 7K');
    expect(html).toContain('exact coordinate population');
  });

  it('does not understate a cluster that crosses the client marker cutoff', () => {
    const points = Array.from({ length: 1_250 }, (_, index) => ({
      ...drilldown.points[0],
      borrower_id: `B-${String(index).padStart(13, '0')}`,
      coordinate_total: 1_700,
    }));
    const html = renderToStaticMarkup(
      <MemoryRouter>
        <EquitySpreadPointsView thresholds={THRESHOLDS} payload={{
          ...drilldown,
          points,
          showing: points.length,
          total_matching: 1_700,
          truncated: true,
        }} />
      </MemoryRouter>,
    );

    expect(html).toContain('1700 borrowers; showing 1250 ranked links returned under server cap');
    expect(html).not.toContain('1250 borrowers at 42% equity and 88 bps spread');
  });

  it('opens a cluster from its native button and Escape closes it back to the marker', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      act(() => {
        root.render(
          <MemoryRouter>
            <EquitySpreadPointsView thresholds={THRESHOLDS} payload={collisionDrilldown} />
          </MemoryRouter>,
        );
      });
      const marker = container.querySelector<HTMLButtonElement>('.analytics-scatter__cluster-marker');
      const panel = container.querySelector<HTMLElement>('.analytics-scatter__cluster-panel');
      expect(marker).not.toBeNull();
      expect(panel).not.toBeNull();
      expect(panel!.hidden).toBe(true);

      // A mouse click focuses the button in Chromium and Firefox; happy-dom's
      // click() does not, so focus it the way the browser would. A real
      // Escape keypress targets the focused element.
      marker!.focus();
      act(() => marker!.click());
      expect(marker!.getAttribute('aria-expanded')).toBe('true');
      expect(panel!.hidden).toBe(false);
      expect(panel!.querySelectorAll('a')).toHaveLength(2);

      act(() => {
        (document.activeElement ?? document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });
      expect(marker!.getAttribute('aria-expanded')).toBe('false');
      expect(panel!.hidden).toBe(true);
      expect(document.activeElement).toBe(marker);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });

  it('puts an open cluster on the Escape stack: a higher layer takes Escape first, then Escape closes it back to the marker (a11y-07)', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const baseline = escapeLayerCount();
    try {
      act(() => {
        root.render(
          <MemoryRouter>
            <EquitySpreadPointsView thresholds={THRESHOLDS} payload={collisionDrilldown} />
          </MemoryRouter>,
        );
      });
      const marker = container.querySelector<HTMLButtonElement>('.analytics-scatter__cluster-marker')!;
      const panel = container.querySelector<HTMLElement>('.analytics-scatter__cluster-panel')!;
      expect(escapeLayerCount()).toBe(baseline);
      act(() => marker.click());
      expect(escapeLayerCount()).toBe(baseline + 1);

      // A layer opened above the cluster (a drawer, the palette) takes the key.
      let higherTook = 0;
      const popHigher = pushEscapeLayer(() => {
        higherTook += 1;
      });
      const escape = () => act(() => {
        (document.activeElement ?? document.body).dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape', bubbles: true }));
      });
      panel.querySelector<HTMLAnchorElement>('a')?.focus();
      escape();
      expect(higherTook).toBe(1);
      expect(panel.hidden).toBe(false);
      popHigher();

      escape();
      expect(panel.hidden).toBe(true);
      expect(marker.getAttribute('aria-expanded')).toBe('false');
      expect(document.activeElement).toBe(marker);
      expect(escapeLayerCount()).toBe(baseline);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });

  it('declines Escape while focus is outside an open cluster: a lower non-modal layer keeps the key and focus stays put (a11y-07)', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    // A Genie-like non-modal layer opened BEFORE the cluster. It declines
    // unless focus is inside it (the useGeniePanelDismissal contract).
    const lower = document.createElement('div');
    const composer = document.createElement('input');
    lower.appendChild(composer);
    document.body.appendChild(lower);
    const pageField = document.createElement('input');
    document.body.appendChild(pageField);
    let lowerTook = 0;
    const baseline = escapeLayerCount();
    const popLower = pushEscapeLayer(() => {
      if (!lower.contains(document.activeElement)) return false;
      lowerTook += 1;
      return undefined;
    });
    const escapeFrom = (target: Element) => {
      const event = new KeyboardEvent('keydown', { key: 'Escape', bubbles: true, cancelable: true });
      act(() => {
        target.dispatchEvent(event);
      });
      return event;
    };
    try {
      act(() => {
        root.render(
          <MemoryRouter>
            <EquitySpreadPointsView thresholds={THRESHOLDS} payload={collisionDrilldown} />
          </MemoryRouter>,
        );
      });
      const marker = container.querySelector<HTMLButtonElement>('.analytics-scatter__cluster-marker')!;
      const panel = container.querySelector<HTMLElement>('.analytics-scatter__cluster-panel')!;
      marker.focus();
      act(() => marker.click());
      expect(panel.hidden).toBe(false);
      expect(escapeLayerCount()).toBe(baseline + 2);

      // Escape in the lower layer's input: that layer handles it, the cluster
      // stays open and focus is not pulled back to the marker.
      composer.focus();
      expect(document.activeElement).toBe(composer);
      escapeFrom(composer);
      expect(lowerTook).toBe(1);
      expect(panel.hidden).toBe(false);
      expect(marker.getAttribute('aria-expanded')).toBe('true');
      expect(document.activeElement).toBe(composer);

      // Escape in an unrelated page field: no layer claims it, so the key
      // reaches the page unconsumed and nothing moves.
      pageField.focus();
      const pageEscape = escapeFrom(pageField);
      expect(pageEscape.defaultPrevented).toBe(false);
      expect(lowerTook).toBe(1);
      expect(panel.hidden).toBe(false);
      expect(marker.getAttribute('aria-expanded')).toBe('true');
      expect(document.activeElement).toBe(pageField);

      // Back inside the cluster, Escape still closes it to its marker.
      panel.querySelector<HTMLAnchorElement>('a')!.focus();
      escapeFrom(document.activeElement!);
      expect(lowerTook).toBe(1);
      expect(panel.hidden).toBe(true);
      expect(marker.getAttribute('aria-expanded')).toBe('false');
      expect(document.activeElement).toBe(marker);
      expect(escapeLayerCount()).toBe(baseline + 1);
    } finally {
      popLower();
      act(() => root.unmount());
      container.remove();
      lower.remove();
      pageField.remove();
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
            <EquitySpreadPointsView thresholds={THRESHOLDS} payload={collisionDrilldown} />
          </MemoryRouter>,
        );
      });
      const markers = [...container.querySelectorAll<HTMLElement>('[data-scatter-marker]')];
      expect(markers).toHaveLength(2);
      expect(markers.filter((marker) => marker.tabIndex === 0)).toHaveLength(1);
      expect(markers.filter((marker) => marker.tabIndex === -1)).toHaveLength(1);
      markers[0].focus();
      act(() => markers[0].dispatchEvent(new KeyboardEvent('keydown', {
        key: 'ArrowRight', bubbles: true,
      })));
      expect(document.activeElement).toBe(markers[1]);
      expect(markers[0].tabIndex).toBe(-1);
      expect(markers[1].tabIndex).toBe(0);
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

  it('keeps a 1,200-point scatter to one Tab stop', () => {
    const points = Array.from({ length: 1_200 }, (_, index) => ({
      ...drilldown.points[0],
      borrower_id: `B-${String(index).padStart(13, '0')}`,
      equity_pct: 40 + (index / 10_000),
      rate_spread_bps: 75 + (index / 10_000),
      coordinate_total: 1,
    }));
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    try {
      act(() => root.render(
        <MemoryRouter>
          <EquitySpreadPointsView thresholds={THRESHOLDS} payload={{
            ...drilldown,
            points,
            showing: points.length,
            total_matching: points.length,
          }} />
        </MemoryRouter>,
      ));
      const markers = [...container.querySelectorAll<HTMLElement>('[data-scatter-marker]')];
      expect(markers).toHaveLength(1_200);
      expect(markers.filter((marker) => marker.tabIndex === 0)).toHaveLength(1);
      expect(markers.filter((marker) => marker.tabIndex === -1)).toHaveLength(1_199);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });

  it('keeps scatter loading height stable while reducing the chart height on narrow containers', () => {
    const css = [
      designCss(),
      readFileSync(join(process.cwd(), 'src', 'routes', 'analytics.scatter.css'), 'utf8'),
    ].join('\n');

    expect(css).toMatch(/\.analytics-chart-panel--scatter\s*\{[^}]*min-height:/s);
    expect(css).toMatch(
      /@container main \(max-width: 640px\)[\s\S]*?\.analytics-chart-panel--scatter\s*\{[^}]*min-height:\s*calc\(\(var\(--sp-16\) \* 5\) \+ var\(--sp-16\)\)/,
    );
    expect(css).toMatch(
      /@container main \(max-width: 640px\)[\s\S]*?\.analytics-scatter-wrap\s*\{[^}]*--analytics-chart-h:\s*calc\(var\(--sp-16\) \* 5\)/,
    );
  });
});

/** Mount a scatter view into the document; returns its container and an unmount. */
function mountScatter(ui: ReactElement): { container: HTMLElement; unmount: () => void } {
  const container = document.createElement('div');
  document.body.appendChild(container);
  const root = createRoot(container);
  act(() => root.render(<MemoryRouter>{ui}</MemoryRouter>));
  return {
    container,
    unmount: () => {
      act(() => root.unmount());
      container.remove();
    },
  };
}

const tipText = (container: HTMLElement) => {
  const tip = container.querySelector('[data-testid="scatter-tip"]');
  if (!tip) return null;
  return {
    label: tip.querySelector('.analytics-chart__tip-x')?.textContent ?? null,
    detail: tip.querySelector('.analytics-chart__tip-y')?.textContent ?? null,
    hidden: tip.getAttribute('aria-hidden'),
  };
};

const pointer = (target: Element, type: 'pointerover' | 'pointerout', relatedTarget: Element | null = null) =>
  act(() => {
    target.dispatchEvent(new PointerEvent(type, { bubbles: true, relatedTarget }));
  });

describe('Equity scatter tooltip, guides and markers (W5e: dataviz-07 a, dataviz-06 b, critic-08, a11y-05)', () => {
  it('carries no title attribute on any marker, bin, point or cluster', () => {
    for (const ui of [
      <EquitySpreadBinsView key="bins" thresholds={THRESHOLDS} overview={overview} onZoom={() => undefined} />,
      <EquitySpreadPointsView key="points" thresholds={THRESHOLDS} payload={collisionDrilldown} />,
    ]) {
      const { container, unmount } = mountScatter(ui);
      try {
        expect(container.querySelectorAll('[data-scatter-marker]').length).toBeGreaterThan(0);
        expect(container.querySelectorAll('[title]')).toHaveLength(0);
      } finally {
        unmount();
      }
    }
  });

  it('shows the bin tip on hover and on focus, with every fact also in the accessible name', () => {
    const { container, unmount } = mountScatter(
      <EquitySpreadBinsView thresholds={THRESHOLDS} overview={overview} onZoom={() => undefined} />,
    );
    try {
      const bin = container.querySelector<HTMLButtonElement>('.analytics-scatter__bin')!;
      expect(tipText(container)).toBeNull();
      pointer(bin, 'pointerover');
      expect(tipText(container)).toEqual({ label: '12 borrowers', detail: `mean score ${SCORE_BAND_HIGH_MIN} · 8 in the money`, hidden: 'true' });
      expect(bin.getAttribute('aria-label')).toBe(
        `12 borrowers near 40% equity and 75 bps spread, mean score ${SCORE_BAND_HIGH_MIN}, 8 in the money. Zoom in to load real borrowers.`,
      );
      pointer(bin, 'pointerout');
      expect(tipText(container), 'pointerleave clears the tip').toBeNull();
      act(() => bin.focus());
      expect(tipText(container)?.label).toBe('12 borrowers');
      // The tip never takes the pointer or focus: it sits in the markers
      // layer (pointer-events: none) and is not focusable.
      const tip = container.querySelector<HTMLElement>('[data-testid="scatter-tip"]')!;
      expect(tip.closest('.analytics-scatter__points')).not.toBeNull();
      expect(tip.querySelector('a, button, [tabindex]')).toBeNull();
      // Leaving with the pointer does not hide the tip of the focused marker.
      pointer(bin, 'pointerover');
      pointer(bin, 'pointerout');
      expect(tipText(container)?.label).toBe('12 borrowers');
      act(() => bin.blur());
      expect(tipText(container)).toBeNull();
    } finally {
      unmount();
    }
  });

  it('names a point by borrower and a cluster by membership in the tip', () => {
    const { container, unmount } = mountScatter(
      <EquitySpreadPointsView thresholds={THRESHOLDS} payload={collisionDrilldown} />,
    );
    try {
      const cluster = container.querySelector<HTMLButtonElement>('.analytics-scatter__cluster-marker')!;
      act(() => cluster.focus());
      expect(tipText(container)).toEqual({ label: '2 borrowers', detail: 'at the same coordinate', hidden: 'true' });
      const dot = container.querySelector<HTMLAnchorElement>('a.analytics-scatter__dot')!;
      pointer(dot, 'pointerover');
      const point = collisionDrilldown.points[2];
      expect(tipText(container)).toEqual({
        label: point.display_name,
        detail: `${point.segment} · ${point.state} · ${point.equity_pct}% equity · ${point.rate_spread_bps} bps · score ${point.opportunity_score}`,
        hidden: 'true',
      });
      expect(dot.getAttribute('aria-label')).toContain(`${point.display_name}: ${point.segment}, ${point.state}, ${point.equity_pct}% equity`);
      pointer(dot, 'pointerout');
      // The cluster still holds focus, so its tip comes back.
      expect(tipText(container)?.label).toBe('2 borrowers');
    } finally {
      unmount();
    }
  });

  it('moves focus with the arrow keys, Home and End from each marker; the groups carry no handler', () => {
    const { container, unmount } = mountScatter(
      <EquitySpreadPointsView thresholds={THRESHOLDS} payload={collisionDrilldown} />,
    );
    try {
      const group = container.querySelector<HTMLElement>('.analytics-scatter__points')!;
      expect(group.getAttribute('role')).toBe('group');
      expect(group.getAttribute('aria-label')).toBe('Borrower drilldown points');
      const markers = [...container.querySelectorAll<HTMLElement>('[data-scatter-marker]')];
      expect(markers).toHaveLength(2);
      const press = (target: HTMLElement, key: string) =>
        act(() => {
          target.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true }));
        });
      act(() => markers[0].focus());
      press(markers[0], 'ArrowRight');
      expect(document.activeElement).toBe(markers[1]);
      press(markers[1], 'ArrowRight');
      expect(document.activeElement, 'wraps').toBe(markers[0]);
      press(markers[0], 'End');
      expect(document.activeElement).toBe(markers[1]);
      press(markers[1], 'Home');
      expect(document.activeElement).toBe(markers[0]);
      // A key pressed on the group itself (not a marker) moves nothing.
      press(group, 'ArrowRight');
      expect(document.activeElement).toBe(markers[0]);
    } finally {
      unmount();
    }
  });

  it('draws the In the Money guides at the payload thresholds, inside the domain only', () => {
    const { container, unmount } = mountScatter(
      <EquitySpreadBinsView thresholds={THRESHOLDS} overview={overview} onZoom={() => undefined} />,
    );
    try {
      const equity = container.querySelector('line.analytics-scatter__guide[data-guide="equity"]')!;
      const spread = container.querySelector('line.analytics-scatter__guide[data-guide="spread"]')!;
      // Overview layout: equity 0..105 (one bin past 100), spread -100..425.
      expect(Number(equity.getAttribute('x1'))).toBeCloseTo((20 / 105) * 100, 6);
      expect(Number(spread.getAttribute('y1'))).toBeCloseTo(100 - (175 / 525) * 100, 6);
      expect(equity.getAttribute('vector-effect')).toBe('non-scaling-stroke');
      const labels = [...container.querySelectorAll('.analytics-scatter__guide-label')].map((label) => label.textContent);
      expect(labels).toEqual(['Equity ≥ 20%', 'Spread ≥ 75 bps']);
      expect(container.querySelector('.analytics-scatter__guides')?.getAttribute('aria-hidden')).toBe('true');
      expect(container.querySelector('[data-testid="scatter-guide-legend"]')?.textContent).toBe(
        'Dashed lines: the In the Money thresholds (spread ≥ 75 bps, equity ≥ 20%) from this refresh.',
      );
    } finally {
      unmount();
    }
    // A zoom window that excludes both thresholds draws neither line.
    const zoom = mountScatter(<EquitySpreadPointsView thresholds={THRESHOLDS} payload={drilldown} />);
    try {
      // viewport equity 40-44, spread 75-99: the spread guide sits on its lower edge, the equity one is outside.
      expect(zoom.container.querySelector('line[data-guide="equity"]')).toBeNull();
      expect(zoom.container.querySelector('line[data-guide="spread"]')).not.toBeNull();
    } finally {
      zoom.unmount();
    }
  });

  it('draws no guide and says why when the refresh carries no single threshold', () => {
    for (const thresholds of [
      { min_spread_bps: null, min_equity_pct: null, reason: 'not_uniform' },
      { min_spread_bps: null, min_equity_pct: 20, reason: 'not_built' },
    ] satisfies AnalyticsThresholds[]) {
      const { container, unmount } = mountScatter(
        <EquitySpreadBinsView thresholds={thresholds} overview={overview} onZoom={() => undefined} />,
      );
      try {
        expect(container.querySelectorAll('.analytics-scatter__guide')).toHaveLength(0);
        expect(container.querySelectorAll('.analytics-scatter__guide-label')).toHaveLength(0);
        expect(container.querySelector('[data-testid="scatter-guide-legend"]')?.textContent).toBe(
          thresholds.reason === 'not_uniform'
            ? 'No single refi-screen threshold applies to this refresh.'
            : 'The refi-screen threshold is not built in this refresh.',
        );
      } finally {
        unmount();
      }
    }
  });

  it('recolours the guide to the data-guide ink, --text-3', () => {
    const rule = /\.analytics-scatter__guide\s*\{([^}]*)\}/.exec(designCss())?.[1] ?? '';
    expect(rule).toContain('stroke: var(--text-3)');
  });
});
