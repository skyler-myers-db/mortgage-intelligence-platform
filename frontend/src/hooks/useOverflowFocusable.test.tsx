/**
 * @vitest-environment happy-dom
 *
 * useOverflowFocusable (W5e a11y-05): a role="region" scroller takes a tab
 * stop only while it overflows. happy-dom does no layout, so each case pins
 * scrollHeight / clientHeight on the element and drives the one
 * ResizeObserver by hand.
 */
import { act, useRef } from 'react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { mount } from '../test/render';
import { useOverflowFocusable } from './useOverflowFocusable';

type ResizeCallback = () => void;
let observers: Array<{ callback: ResizeCallback; targets: Element[]; disconnected: boolean }> = [];

class StubResizeObserver {
  private readonly entry: { callback: ResizeCallback; targets: Element[]; disconnected: boolean };
  constructor(callback: ResizeCallback) {
    this.entry = { callback, targets: [], disconnected: false };
    observers.push(this.entry);
  }
  observe(target: Element) {
    this.entry.targets.push(target);
  }
  disconnect() {
    this.entry.disconnected = true;
  }
  unobserve() {}
}

const sizes = { scroll: 100, client: 220 };

function Panel({ rows, onRender }: { rows: number; onRender?: () => void }) {
  const ref = useRef<HTMLDivElement | null>(null);
  const scrolls = useOverflowFocusable(ref, rows);
  onRender?.();
  return (
    <div ref={ref} data-testid="panel" role="region" aria-label="Rows" tabIndex={scrolls ? 0 : undefined}>
      {Array.from({ length: rows }, (_, index) => <div key={index}>row {index}</div>)}
    </div>
  );
}

function pinLayout(): void {
  Object.defineProperty(HTMLElement.prototype, 'scrollHeight', { configurable: true, get: () => sizes.scroll });
  Object.defineProperty(HTMLElement.prototype, 'clientHeight', { configurable: true, get: () => sizes.client });
}

const panel = () => document.querySelector<HTMLElement>('[data-testid="panel"]');
const live = () => observers.filter((entry) => !entry.disconnected);

describe('useOverflowFocusable', () => {
  beforeEach(() => {
    observers = [];
    sizes.scroll = 100;
    sizes.client = 220;
    vi.stubGlobal('ResizeObserver', StubResizeObserver);
    pinLayout();
  });

  afterEach(() => {
    vi.unstubAllGlobals();
    Reflect.deleteProperty(HTMLElement.prototype, 'scrollHeight');
    Reflect.deleteProperty(HTMLElement.prototype, 'clientHeight');
  });

  it('gives no tab stop while the content fits', async () => {
    await mount(<Panel rows={2} />);
    expect(panel()?.hasAttribute('tabindex')).toBe(false);
    expect(live()).toHaveLength(1);
    expect(live()[0]?.targets).toEqual([panel()]);
  });

  it('takes a tab stop once the content overflows, re-measured on a row-count change', async () => {
    const { rerender } = await mount(<Panel rows={2} />);
    sizes.scroll = 640;
    await rerender(<Panel rows={25} />);
    expect(panel()?.getAttribute('tabindex')).toBe('0');
    sizes.scroll = 100;
    await rerender(<Panel rows={2} />);
    expect(panel()?.hasAttribute('tabindex')).toBe(false);
  });

  it('flips on the panel\'s own resize, and ignores a 1px rounding overflow', async () => {
    await mount(<Panel rows={8} />);
    sizes.scroll = 221;
    act(() => live()[0]?.callback());
    expect(panel()?.hasAttribute('tabindex'), '1px is rounding, not a scroller').toBe(false);
    sizes.client = 120;
    act(() => live()[0]?.callback());
    expect(panel()?.getAttribute('tabindex')).toBe('0');
  });

  it('renders nothing on a resize that does not flip, and disconnects on unmount', async () => {
    const onRender = vi.fn();
    const { unmount } = await mount(<Panel rows={8} onRender={onRender} />);
    const before = onRender.mock.calls.length;
    act(() => live()[0]?.callback());
    act(() => live()[0]?.callback());
    expect(onRender).toHaveBeenCalledTimes(before);
    unmount();
    expect(live()).toHaveLength(0);
  });
});
