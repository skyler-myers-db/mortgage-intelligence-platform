/**
 * @vitest-environment happy-dom
 *
 * Histogram (dataviz-06 / visual-08 item 2) and, through it, the kit's
 * keyboard cursor (dataviz-07 step 2, dataviz-10): bins as rects, the
 * threshold partition by lower edge, the rule at the exact threshold, the
 * no-threshold notice, the governed 75+ label, and the hover/keyboard readout.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { HIGH_OPPORTUNITY_SCORE_LABEL } from '../../lib/opportunityScore';
import type { ExecutiveAnalyticsResponse } from '../../types';
import { ScoreDistribution } from '../../routes/analytics.sections';
import { Histogram, binIsPast, ruleLabelSide, type HistogramBin } from './Histogram';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const BINS: HistogramBin[] = [
  { start: 0, count: 1200 },
  { start: 25, count: 4300 },
  { start: 50, count: 12480 },
  { start: 75, count: 900 },
];

describe('ruleLabelSide', () => {
  it('keeps the label right of its rule while it fits there', () => {
    expect(ruleLabelSide(40, 400, 150, 4)).toBe('start');
  });
  it('hangs it left when the right is too narrow and the left is wider (the Console-open score rule)', () => {
    expect(ruleLabelSide(68.75, 359, 145, 4)).toBe('end');
  });
  it('takes the wider side when neither fits', () => {
    expect(ruleLabelSide(45, 200, 150, 4)).toBe('start');
    expect(ruleLabelSide(55, 200, 150, 4)).toBe('end');
  });
});

describe('Histogram', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const render = (props: Partial<Parameters<typeof Histogram>[0]> = {}) =>
    act(() =>
      root.render(
        <Histogram
          title="Rate Spread Distribution"
          summary="18,880 borrowers plotted."
          table={<table data-testid="twin" />}
          bins={BINS}
          binWidth={25}
          threshold={60}
          ruleLabel="Refi screen ≥ 60 bps"
          xLabel="Spread bps"
          yLabel="Borrowers"
          formatRange={(start) => `${start}–${start + 24} bps`}
          {...props}
        />,
      ),
    );

  const bars = () => [...container.querySelectorAll<SVGRectElement>('rect.chart-hist__bar')];
  const plot = () => container.querySelector<HTMLElement>('.chart-frame__plot') as HTMLElement;
  const live = () => container.querySelector('[aria-live="polite"]')?.textContent ?? '';
  const tipX = () => container.querySelector('.analytics-chart__tip-x')?.textContent ?? null;
  const key = (name: string) => act(() => plot().dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true })));

  it('draws one rect per bin across [first start, last start + width] in a 0-100 viewBox', () => {
    render();
    expect(bars()).toHaveLength(4);
    expect(container.querySelector('svg.chart-hist')?.getAttribute('preserveAspectRatio')).toBe('none');
    // Domain [0, 100]: each 25-wide bin is 25% of the plot, edge to edge.
    expect(bars().map((bar) => [bar.getAttribute('x'), bar.getAttribute('width')])).toEqual([
      ['0.00', '25.00'],
      ['25.00', '25.00'],
      ['50.00', '25.00'],
      ['75.00', '25.00'],
    ]);
    expect(container.querySelector('polyline')).toBeNull();
  });

  it('plots y over the 1-2-5 domain, so the top gridline is its maximum', () => {
    render();
    const labels = [...container.querySelectorAll('.analytics-chart__tick--y')].map((tick) => tick.textContent);
    expect(labels).toEqual(['15K', '10K', '5K', '0']);
    // The tallest bin (12,480 of 15,000) stays under the top gridline.
    const tallest = bars()[2];
    const gridTop = Math.min(...[...container.querySelectorAll('line.analytics-chart__grid')].map((line) => Number(line.getAttribute('y1'))));
    expect(Number(tallest.getAttribute('y'))).toBeGreaterThan(gridTop);
  });

  it('does not count a bin straddling a non-aligned threshold as past, and draws the rule at the exact value', () => {
    render({ threshold: 60 });
    expect(bars().map((bar) => bar.classList.contains('chart-hist__bar--past'))).toEqual([false, false, false, true]);
    const rule = container.querySelector('line.chart-hist__rule');
    // 60 in [0, 100] is 60% of the plot, inside the 50-74 bin.
    expect(rule?.getAttribute('x1')).toBe('60.00');
    expect(container.querySelector('.chart-hist__rule-label')?.textContent).toBe('Refi screen ≥ 60 bps');
    expect(binIsPast(50, 60)).toBe(false);
    expect(binIsPast(75, 75)).toBe(true);
  });

  it('with no threshold draws no rule and no partition, and shows the notice', () => {
    render({ threshold: null, ruleLabel: null, notice: 'The refi-screen threshold is not built in this refresh.' });
    expect(container.querySelector('.chart-hist__rule')).toBeNull();
    expect(container.querySelector('.chart-hist__rule-label')).toBeNull();
    expect(bars().every((bar) => bar.classList.contains('chart-hist__bar--past'))).toBe(true);
    expect(container.querySelector('.chart-frame__notice')?.textContent).toBe('The refi-screen threshold is not built in this refresh.');
  });

  it('edge-anchors an x tick that sits on the plot edge', () => {
    render();
    const ticks = [...container.querySelectorAll('.analytics-chart__tick--x')];
    expect(ticks[0].className).toContain('analytics-chart__tick--edge-start');
    expect(ticks[ticks.length - 1].className).toContain('analytics-chart__tick--edge-end');
  });

  it('shows the first bin on focus and walks the bins with the keyboard, announcing only keyboard moves', () => {
    render();
    expect(plot().tabIndex).toBe(0);
    expect(tipX()).toBeNull();
    act(() => plot().focus());
    expect(tipX()).toBe('0–24 bps');
    expect(live()).toBe('');
    key('ArrowRight');
    key('ArrowRight');
    key('ArrowRight');
    expect(tipX()).toBe('75–99 bps');
    expect(live()).toBe('75–99 bps: 900 borrowers');
    key('ArrowRight');
    expect(live()).toBe('75–99 bps: 900 borrowers');
    key('Home');
    expect(live()).toBe('0–24 bps: 1,200 borrowers');
    key('End');
    expect(live()).toBe('75–99 bps: 900 borrowers');
    key('ArrowLeft');
    expect(live()).toBe('50–74 bps: 12,480 borrowers');
    act(() => plot().blur());
    expect(tipX()).toBeNull();
    expect(live()).toBe('');
  });

  it('snaps the pointer to the nearest bin without touching the live region', () => {
    render();
    const layer = container.querySelector('.analytics-chart__hover') as HTMLElement;
    expect(layer.getAttribute('aria-hidden')).toBe('true');
    layer.getBoundingClientRect = () => ({ left: 0, width: 400, top: 0, height: 200, right: 400, bottom: 200, x: 0, y: 0 }) as DOMRect;
    act(() => {
      const event = new Event('pointermove', { bubbles: true });
      Object.defineProperty(event, 'clientX', { value: 0.66 * 400 });
      layer.dispatchEvent(event);
    });
    expect(tipX()).toBe('50–74 bps');
    expect(container.querySelector('.analytics-chart__tip-y')?.textContent).toBe('12,480 borrowers');
    expect(container.querySelector('.analytics-chart__tip')?.className).toContain('--flip');
    expect(live()).toBe('');
    act(() => {
      const event = new Event('pointerout', { bubbles: true });
      Object.defineProperty(event, 'relatedTarget', { value: null });
      layer.dispatchEvent(event);
    });
    expect(tipX()).toBeNull();
  });
});

describe('Opportunity Score Distribution', () => {
  it('labels the rule with the governed 75+ count, not the re-summed bins', () => {
    const container = document.createElement('div');
    document.body.appendChild(container);
    const root = createRoot(container);
    const data = {
      totals: { addressable_borrowers: 1_000, high_opportunity_borrowers: 180 },
      // The bins at 75+ sum to 300, which the label must not print.
      score_distribution: [
        { score_bucket: 60, borrower_count: 400 },
        { score_bucket: 70, borrower_count: 300 },
        { score_bucket: 75, borrower_count: 200 },
        { score_bucket: 80, borrower_count: 100 },
      ],
    } as unknown as ExecutiveAnalyticsResponse;
    try {
      act(() => root.render(<ScoreDistribution data={data} />));
      expect(container.querySelector('.chart-hist__rule-label')?.textContent).toBe(`${HIGH_OPPORTUNITY_SCORE_LABEL} · 180 borrowers`);
      expect(container.querySelector('figcaption')?.textContent).toContain(`180 (18.0%) score ${HIGH_OPPORTUNITY_SCORE_LABEL}`);
      expect(container.textContent).not.toContain('300 borrowers');
      const past = [...container.querySelectorAll('rect.chart-hist__bar')].map((bar) => bar.classList.contains('chart-hist__bar--past'));
      expect(past).toEqual([false, false, true, true]);
    } finally {
      act(() => root.unmount());
      container.remove();
    }
  });
});
