/**
 * @vitest-environment happy-dom
 *
 * useTableScrollClearance (audit a11y-v2): measures the sticky thead, the
 * pinned Approval header and the sticky route nav, and writes them as the
 * custom properties the scroll-padding rules read (03-score-and-table.css,
 * LeadTable.css, 38-focus-clearance.css). A resize is written in the next
 * animation frame, never inside the ResizeObserver callback; all three are
 * removed on unmount. The rendered proof (no focus stop under the chrome) is
 * the focus-obscured walk in lead-queue.fixture.spec.ts.
 */
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  ROUTE_NAV_BLOCK_VAR,
  TABLE_HEAD_BLOCK_VAR,
  TABLE_PIN_INLINE_VAR,
  useTableScrollClearance,
} from './useTableScrollClearance';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sizes = { head: 44.4, pin: 156, nav: 57 };
const observed: Element[] = [];
let observerCallback: (() => void) | null = null;
const frames: FrameRequestCallback[] = [];

class FakeResizeObserver {
  constructor(callback: () => void) {
    observerCallback = callback;
  }
  observe(target: Element) {
    observed.push(target);
  }
  disconnect() {
    observerCallback = null;
  }
  unobserve() {}
}

function Table({ layoutKey = 'default' }: { layoutKey?: string }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  useTableScrollClearance(wrapRef, layoutKey);
  return (
    <div ref={wrapRef} className="tbl-wrap">
      <table className="lead-table__table">
        <thead>
          <tr>
            <th>Borrower</th>
            <th className="lead-table__approval-header">Approval</th>
          </tr>
        </thead>
      </table>
    </div>
  );
}

describe('useTableScrollClearance', () => {
  let root: Root;
  let main: HTMLElement;

  beforeEach(() => {
    observed.length = 0;
    frames.length = 0;
    observerCallback = null;
    sizes.head = 44.4;
    sizes.pin = 156;
    sizes.nav = 57;
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function rect(this: HTMLElement) {
      const height = this.tagName === 'THEAD' ? sizes.head : 0;
      const width = this.classList.contains('lead-table__approval-header') ? sizes.pin : 0;
      return { width, height, top: 0, left: 0, right: width, bottom: height, x: 0, y: 0, toJSON: () => ({}) } as DOMRect;
    });
    vi.spyOn(HTMLElement.prototype, 'offsetHeight', 'get').mockImplementation(function height(this: HTMLElement) {
      return this.classList.contains('route-nav') ? sizes.nav : 0;
    });
    document.body.innerHTML = '<main class="main"><nav class="route-nav"></nav><div id="root"></div></main>';
    main = document.querySelector<HTMLElement>('.main')!;
    root = createRoot(document.getElementById('root') as HTMLElement);
  });

  afterEach(() => {
    act(() => root.unmount());
    document.body.innerHTML = '';
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const wrap = () => document.querySelector<HTMLElement>('.tbl-wrap')!;

  it('writes the thead, pin and route-nav sizes on mount (rounded up to whole pixels)', () => {
    act(() => root.render(<Table />));
    expect(wrap().style.getPropertyValue(TABLE_HEAD_BLOCK_VAR)).toBe('45px');
    expect(wrap().style.getPropertyValue(TABLE_PIN_INLINE_VAR)).toBe('156px');
    expect(main.style.getPropertyValue(ROUTE_NAV_BLOCK_VAR)).toBe('57px');
    expect(observed.map((element) => element.tagName.toLowerCase())).toEqual(['thead', 'th', 'nav']);
  });

  it('writes a resize in the next animation frame, never inside the observer callback', () => {
    act(() => root.render(<Table />));
    sizes.head = 88;
    sizes.nav = 90;
    act(() => observerCallback?.());
    expect(wrap().style.getPropertyValue(TABLE_HEAD_BLOCK_VAR), 'unchanged inside the callback').toBe('45px');
    expect(frames).toHaveLength(1);
    act(() => observerCallback?.());
    expect(frames, 'one frame per burst').toHaveLength(1);

    act(() => frames.shift()?.(0));

    expect(wrap().style.getPropertyValue(TABLE_HEAD_BLOCK_VAR)).toBe('88px');
    expect(main.style.getPropertyValue(ROUTE_NAV_BLOCK_VAR)).toBe('90px');
  });

  it('removes all three properties on unmount', () => {
    act(() => root.render(<Table />));
    const table = wrap();
    act(() => root.render(<div />));
    expect(table.style.getPropertyValue(TABLE_HEAD_BLOCK_VAR)).toBe('');
    expect(table.style.getPropertyValue(TABLE_PIN_INLINE_VAR)).toBe('');
    expect(main.style.getPropertyValue(ROUTE_NAV_BLOCK_VAR)).toBe('');
  });

  it('writes nothing for a route nav that is not rendered (0 high)', () => {
    sizes.nav = 0;
    act(() => root.render(<Table />));
    expect(main.style.getPropertyValue(ROUTE_NAV_BLOCK_VAR)).toBe('');
  });
});
