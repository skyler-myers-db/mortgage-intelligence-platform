/**
 * @vitest-environment happy-dom
 *
 * The kit cursor's focus contract (2026-09-21 audit dataviz-07, the W5e
 * fix-round leftover): a pointer press on a plot focuses it, and focus shows
 * the first point; that index-0 tip used to stay up after the pointer left.
 * Driven through the real DOM events on a plot and its tooltip layer, the
 * way CountChart, Histogram and GenieLineChart attach the handlers.
 */
import { act, createElement } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it } from 'vitest';
import { useChartCursor } from './useChartCursor';

function Probe({ count }: { count: number }) {
  const cursor = useChartCursor(count, Array.from({ length: count }, (_, i) => (i / Math.max(1, count - 1)) * 100));
  // The plot (ChartFrame's .chart-frame__plot) holds the tooltip layer; the
  // cursor's two indexes are rendered so the test reads what a render saw.
  return createElement(
    'div',
    { 'data-testid': 'plot', 'data-index': cursor.index ?? 'none', 'data-live': cursor.liveIndex ?? 'none', ...cursor.plotProps },
    createElement('div', { 'data-testid': 'surface', ...cursor.surfaceProps }),
  );
}

describe('useChartCursor focus, pointer and keyboard (dataviz-07)', () => {
  let container: HTMLDivElement;
  let root: Root;
  const plot = () => container.querySelector<HTMLElement>('[data-testid="plot"]')!;
  const read = (attr: 'data-index' | 'data-live'): number | null => {
    const value = plot().getAttribute(attr);
    return value === null || value === 'none' ? null : Number(value);
  };
  const latest = { get index() { return read('data-index'); }, get liveIndex() { return read('data-live'); } };
  const surface = () => container.querySelector<HTMLElement>('[data-testid="surface"]')!;
  const fire = (target: HTMLElement, event: Event) => act(() => {
    target.dispatchEvent(event);
  });
  const pointerDownAndFocus = () => {
    fire(surface(), new PointerEvent('pointerdown', { bubbles: true }));
    act(() => plot().focus());
    fire(surface(), new PointerEvent('pointerup', { bubbles: true }));
  };
  const pointerLeave = () => fire(surface(), new PointerEvent('pointerout', { bubbles: true, relatedTarget: document.body }));
  const key = (name: string) => fire(plot(), new KeyboardEvent('keydown', { key: name, bubbles: true }));

  beforeEach(() => {
    container = document.body.appendChild(document.createElement('div'));
    root = createRoot(container);
    act(() => root.render(createElement(Probe, { count: 4 })));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  it('clears the index-0 cursor a pointer press made once the pointer leaves', () => {
    pointerDownAndFocus();
    expect(document.activeElement).toBe(plot());
    expect(latest.index, 'focus from the press shows the first point').toBe(0);
    pointerLeave();
    expect(latest.index, 'pointerleave hides the tip the press made').toBeNull();
    expect(latest.liveIndex).toBeNull();
  });

  it('keeps the index-0 cursor that Tab focus made through a pointerleave', () => {
    act(() => plot().focus());
    expect(latest.index).toBe(0);
    pointerLeave();
    expect(latest.index).toBe(0);
    expect(latest.liveIndex, 'focus alone announces nothing').toBeNull();
  });

  it('keeps a keyboard-moved cursor through a pointerleave, even after a press', () => {
    pointerDownAndFocus();
    key('ArrowRight');
    expect(latest.index).toBe(1);
    expect(latest.liveIndex, 'a key move is announced').toBe(1);
    pointerLeave();
    expect(latest.index).toBe(1);
    expect(latest.liveIndex).toBe(1);
  });

  it('a press on an already-focused plot does not turn a later Tab focus into a pointer one', () => {
    act(() => plot().focus());
    fire(surface(), new PointerEvent('pointerdown', { bubbles: true }));
    fire(surface(), new PointerEvent('pointerup', { bubbles: true }));
    act(() => plot().blur());
    expect(latest.index).toBeNull();
    act(() => plot().focus());
    pointerLeave();
    expect(latest.index).toBe(0);
  });

  it('blur clears the cursor, and Home / End walk the points', () => {
    act(() => plot().focus());
    key('End');
    expect(latest.liveIndex).toBe(3);
    key('Home');
    expect(latest.liveIndex).toBe(0);
    act(() => plot().blur());
    expect(latest.index).toBeNull();
    expect(latest.liveIndex).toBeNull();
  });
});
