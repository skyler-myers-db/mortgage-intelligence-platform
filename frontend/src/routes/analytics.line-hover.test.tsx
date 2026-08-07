/**
 * @vitest-environment happy-dom
 *
 * Pointer hover readout on the distribution LineChart (Rate Spread /
 * Opportunity Score). Covers nearest-point selection, the formatted x/y
 * readout, teardown on pointer leave, and the a11y contract (the hover layer
 * must stay invisible to assistive tech).
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { RateSpreadBucket } from '../types';
import { LineChart } from './analytics.charts';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const ROWS = [
  { spread_bucket_bps: 0, borrower_count: 1200 },
  { spread_bucket_bps: 50, borrower_count: 4300 },
  { spread_bucket_bps: 100, borrower_count: 12480 },
  { spread_bucket_bps: 150, borrower_count: 900 },
] as unknown as RateSpreadBucket[];

describe('LineChart hover readout', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => {
      root.render(
        <LineChart
          rows={ROWS}
          x={(row) => (row as RateSpreadBucket).spread_bucket_bps}
          y={(row) => row.borrower_count}
          xLabel="Spread bps"
          yLabel="Borrowers"
          xUnit="bps"
        />,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  const layer = () => container.querySelector('.analytics-chart__hover') as HTMLElement;

  /** Drive a pointer move at `ratio` (0..1) across a stubbed 400px-wide layer. */
  const hoverAt = (ratio: number) => {
    const el = layer();
    el.getBoundingClientRect = () =>
      ({ left: 0, width: 400, top: 0, height: 200, right: 400, bottom: 200, x: 0, y: 0 }) as DOMRect;
    act(() => {
      const event = new Event('pointermove', { bubbles: true });
      Object.defineProperty(event, 'clientX', { value: ratio * 400 });
      el.dispatchEvent(event);
    });
  };

  it('keeps the hover layer out of the accessibility tree', () => {
    expect(layer().getAttribute('aria-hidden')).toBe('true');
    expect(layer().querySelector('button, a, [tabindex]')).toBeNull();
    // The chart's own meaning still comes from the parent role="img".
    expect(container.querySelector('.analytics-chart')?.getAttribute('role')).toBe('img');
  });

  it('renders no crosshair until the pointer enters the plot', () => {
    expect(container.querySelector('.analytics-chart__tip')).toBeNull();
    expect(container.querySelector('.analytics-chart__hover-dot')).toBeNull();
  });

  it('snaps to the nearest point and formats both axes', () => {
    hoverAt(0.68); // closest to the 100 bps bucket
    const tip = container.querySelector('.analytics-chart__tip');
    expect(tip?.querySelector('.analytics-chart__tip-x')?.textContent).toBe('100 bps');
    expect(tip?.querySelector('.analytics-chart__tip-y')?.textContent).toBe('12,480 borrowers');
    expect(container.querySelector('.analytics-chart__hover-dot')).not.toBeNull();
    expect(container.querySelector('.analytics-chart__crosshair')).not.toBeNull();
  });

  it('formats the borrower count with thousands separators', () => {
    hoverAt(0.34); // 50 bps bucket
    expect(container.querySelector('.analytics-chart__tip-y')?.textContent).toBe('4,300 borrowers');
  });

  it('snaps to the first and last points at the plot edges', () => {
    hoverAt(0);
    expect(container.querySelector('.analytics-chart__tip-x')?.textContent).toBe('0 bps');
    hoverAt(1);
    expect(container.querySelector('.analytics-chart__tip-x')?.textContent).toBe('150 bps');
  });

  it('flips the tip away from the right edge so it cannot overflow the surface', () => {
    hoverAt(0.05);
    expect(container.querySelector('.analytics-chart__tip')?.className).not.toContain('--flip');
    hoverAt(1);
    expect(container.querySelector('.analytics-chart__tip')?.className).toContain('--flip');
  });

  it('clears the readout when the pointer leaves', () => {
    hoverAt(0.5);
    expect(container.querySelector('.analytics-chart__tip')).not.toBeNull();
    act(() => {
      // React synthesizes onPointerLeave from a bubbling `pointerout` whose
      // relatedTarget is outside the element — `pointerleave` itself does not
      // bubble, so dispatching it directly would never reach React.
      const event = new Event('pointerout', { bubbles: true });
      Object.defineProperty(event, 'relatedTarget', { value: null });
      layer().dispatchEvent(event);
    });
    expect(container.querySelector('.analytics-chart__tip')).toBeNull();
  });
});
