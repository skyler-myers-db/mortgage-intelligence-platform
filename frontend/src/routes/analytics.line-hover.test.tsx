/**
 * @vitest-environment happy-dom
 *
 * Evidence Events Per Day on the chart kit (audit 2026-09-21 dataviz-v2,
 * stack-06, dataviz-10): each day sits at its UTC-day offset, never its
 * index, so a missing day keeps its width; the hover and keyboard readout
 * names the date and the count; the frame carries the summary and the
 * date/events table. The hover layer stays out of the accessibility tree.
 */
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import type { DailyEvidenceTotal } from './analytics.lib';
import { DailyEvidenceLineChart } from './analytics.charts';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/** May 1, 2 and 5: two days are missing between the second and third rows. */
const GAPPED: DailyEvidenceTotal[] = [
  { event_date: '2026-05-01', event_count: 40 },
  { event_date: '2026-05-02', event_count: 1_250 },
  { event_date: '2026-05-05', event_count: 300 },
];

describe('DailyEvidenceLineChart', () => {
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

  const render = (rows: DailyEvidenceTotal[] = GAPPED) => act(() => root.render(<DailyEvidenceLineChart rows={rows} />));
  const layer = () => container.querySelector('.analytics-chart__hover') as HTMLElement;
  const plot = () => container.querySelector<HTMLElement>('.chart-frame__plot') as HTMLElement;
  const tipX = () => container.querySelector('.analytics-chart__tip-x')?.textContent ?? null;
  const tipY = () => container.querySelector('.analytics-chart__tip-y')?.textContent ?? null;
  const live = () => container.querySelector('[aria-live="polite"]')?.textContent ?? '';
  const key = (name: string) => act(() => plot().dispatchEvent(new KeyboardEvent('keydown', { key: name, bubbles: true })));

  /** Drive a pointer move at `ratio` (0..1) across a stubbed 400px-wide layer. */
  const hoverAt = (ratio: number) => {
    const el = layer();
    el.getBoundingClientRect = () => ({ left: 0, width: 400, top: 0, height: 200, right: 400, bottom: 200, x: 0, y: 0 }) as DOMRect;
    act(() => {
      const event = new Event('pointermove', { bubbles: true });
      Object.defineProperty(event, 'clientX', { value: ratio * 400 });
      el.dispatchEvent(event);
    });
  };

  it('plots each day at its UTC-day offset: May 1, 2 and 5 sit at 0, 25 and 100%', () => {
    render();
    const xs = (container.querySelector('polyline')?.getAttribute('points') ?? '')
      .split(' ')
      .map((pair) => pair.split(',')[0]);
    expect(xs).toEqual(['0.00', '25.00', '100.00']);
    const ticks = [...container.querySelectorAll<HTMLElement>('.analytics-chart__tick--x')];
    expect(ticks.map((tick) => [tick.textContent, tick.style.getPropertyValue('--tick-pos')])).toEqual([
      ['May 1', '0%'],
      ['May 2', '25%'],
      ['May 5', '100%'],
    ]);
  });

  it('anchors the first date label at the left edge and the last at the right edge', () => {
    render();
    const ticks = [...container.querySelectorAll<HTMLElement>('.analytics-chart__tick--x')];
    expect(ticks[0].classList.contains('analytics-chart__tick--edge-start')).toBe(true);
    expect(ticks[1].className).not.toContain('--edge');
    expect(ticks[2].classList.contains('analytics-chart__tick--edge-end')).toBe(true);
  });

  it('plots y over the 1-2-5 count domain, so the top gridline is its maximum', () => {
    render();
    const labels = [...container.querySelectorAll('.analytics-chart__tick--y')].map((tick) => tick.textContent);
    expect(labels).toEqual(['1.5K', '1K', '500', '0']);
  });

  it('snaps the pointer to the nearest day and reads its date and count', () => {
    render();
    expect(container.querySelector('.analytics-chart__tip')).toBeNull();
    hoverAt(0.3);
    expect(tipX()).toBe('May 2');
    expect(tipY()).toBe('1,250 events');
    // The tooltip sits on the day's position, not its index (index 1 of 3 would be 50%).
    expect(container.querySelector<HTMLElement>('.analytics-chart__tip')?.style.getPropertyValue('--hover-x')).toBe('25%');
    hoverAt(0.9);
    expect(tipX()).toBe('May 5');
    // A pointer sweep never floods the live region.
    expect(live()).toBe('');
  });

  it('walks the days from the keyboard and announces each move', () => {
    render();
    act(() => plot().focus());
    expect(tipX()).toBe('May 1');
    expect(live()).toBe('');
    key('ArrowRight');
    expect(live()).toBe('May 2: 1,250 events');
    key('End');
    expect(live()).toBe('May 5: 300 events');
    key('Home');
    expect(tipX()).toBe('May 1');
    act(() => plot().blur());
    expect(container.querySelector('.analytics-chart__tip')).toBeNull();
  });

  it('is a figure: the plot is a named group described by the summary, the hover layer is aria-hidden', () => {
    render();
    expect(container.querySelector('[role="img"]')).toBeNull();
    const figure = container.querySelector('figure.chart-frame');
    const caption = figure?.querySelector('figcaption');
    expect(caption?.textContent).toBe('1,590 evidence events from May 1, 2026 to May 5, 2026; the busiest day was May 2, 2026 with 1,250.');
    expect(plot().getAttribute('role')).toBe('group');
    expect(plot().getAttribute('aria-label')).toBe('Evidence Events Per Day');
    expect(plot().getAttribute('aria-describedby')).toBe(caption?.id);
    expect(layer().getAttribute('aria-hidden')).toBe('true');
  });

  it('lists the plotted days, date and events, in the table view', () => {
    render();
    const toggle = container.querySelector<HTMLButtonElement>('button.chart-frame__toggle');
    expect(toggle?.textContent).toBe('View as table');
    act(() => toggle?.click());
    const cells = [...container.querySelectorAll('tbody tr')].map((tr) => [...tr.querySelectorAll('td')].map((td) => td.textContent));
    expect(cells).toEqual([
      ['May 1, 2026', '40'],
      ['May 2, 2026', '1,250'],
      ['May 5, 2026', '300'],
    ]);
    expect([...container.querySelectorAll('thead th')].map((th) => th.textContent)).toEqual(['Date', 'Events']);
  });

  it('puts a single day in the middle and says so in one sentence', () => {
    render([{ event_date: '2026-05-03', event_count: 12 }]);
    expect(container.querySelector('polyline')?.getAttribute('points')?.split(',')[0]).toBe('50.00');
    expect(container.querySelector('figcaption')?.textContent).toBe('12 evidence events on May 3, 2026.');
  });

  it('says so when there is no evidence', () => {
    render([]);
    expect(container.textContent).toBe('No daily evidence returned.');
    expect(container.querySelector('figure')).toBeNull();
  });
});
