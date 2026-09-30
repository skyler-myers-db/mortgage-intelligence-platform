/**
 * @vitest-environment happy-dom
 *
 * CountChart under the React Compiler (the w4-charts review item
 * "compiled shells"): the kit compiles as written, and a cursor move
 * re-renders the tooltip and the live text, not the plot. The marks callback
 * runs when the points or the marks change and never on a pointer or key
 * move, so the bars, gridlines and ticks are kept between moves.
 */
import { act, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { CountChart, type CountChartPoint } from './CountChart';
import type { Scale } from './scales';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const POINTS: CountChartPoint[] = [
  { x: 0, count: 4, label: 'May 1' },
  { x: 50, count: 9, label: 'May 3' },
  { x: 100, count: 2, label: 'May 5' },
];

describe('CountChart', () => {
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

  const render = (points: CountChartPoint[], marks: (y: Scale) => ReactNode) =>
    act(() =>
      root.render(
        <CountChart
          title="Evidence Events Per Day"
          summary="15 evidence events."
          table={<table />}
          points={points}
          xTicks={points.map((point) => ({ x: point.x, label: point.label }))}
          xLabel="Event date"
          yLabel="Events"
          svgClassName="analytics-line-chart"
          marks={marks}
        />,
      ),
    );

  const plot = () => container.querySelector<HTMLElement>('.chart-frame__plot') as HTMLElement;
  const tipX = () => container.querySelector('.analytics-chart__tip-x')?.textContent ?? null;
  const key = (name: string) => act(() => plot().dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true })));
  const pointAt = (pct: number) =>
    act(() => {
      const layer = container.querySelector('.analytics-chart__hover') as HTMLElement;
      layer.getBoundingClientRect = () => ({ left: 0, width: 400, top: 0, height: 200, right: 400, bottom: 200, x: 0, y: 0 }) as DOMRect;
      const event = new Event('pointermove', { bubbles: true });
      Object.defineProperty(event, 'clientX', { value: (pct / 100) * 400 });
      layer.dispatchEvent(event);
    });

  it('keeps the plot across pointer and keyboard moves: the marks callback does not run again', () => {
    const marks = vi.fn((y: Scale) => <polyline points={POINTS.map((point) => `${point.x},${y(point.count)}`).join(' ')} />);
    render(POINTS, marks);
    expect(marks).toHaveBeenCalledTimes(1);

    act(() => plot().focus());
    key('ArrowRight');
    key('End');
    expect(tipX()).toBe('May 5');
    pointAt(48);
    expect(tipX()).toBe('May 3');
    expect(container.querySelector('.analytics-chart__tip-y')?.textContent).toBe('9 events');

    // Five cursor moves, one plot build.
    expect(marks).toHaveBeenCalledTimes(1);
    expect(container.querySelectorAll('line.analytics-chart__grid').length).toBeGreaterThanOrEqual(3);
  });

  it('rebuilds the plot when the points change, and places the tip on the point row', () => {
    const marks = vi.fn(() => null);
    render(POINTS, marks);
    render([...POINTS, { x: 100, count: 20, label: 'May 6' }], marks);
    expect(marks).toHaveBeenCalledTimes(2);
    act(() => plot().focus());
    key('End');
    const tip = container.querySelector<HTMLElement>('.analytics-chart__tip');
    // 20 is the domain maximum (the top gridline, the plot's 6% row).
    expect(tip?.style.getPropertyValue('--hover-y')).toBe('6%');
  });
});
