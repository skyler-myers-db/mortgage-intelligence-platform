/**
 * @vitest-environment happy-dom
 *
 * useLeadTableFillHeight measures the Lead Queue scroller from its own top
 * edge to the fold of `<main>`, for the SCREEN only. print.css lays `<main>`
 * out unclipped, so a frame in print media measured the paper and the
 * scroller came back from the print unclipped; the virtualized queue's
 * afterprint restore then clamped to an empty scroll range (W5e CI run
 * 40311237233). The rendered proof is lead-queue-print.fixture.spec.ts.
 */
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LEAD_TABLE_FILL_VAR, useLeadTableFillHeight } from './useLeadTableFillHeight';

let observerCallback: (() => void) | null = null;
const frames: FrameRequestCallback[] = [];
const layout = { mainHeight: 900, printing: false };

class FakeResizeObserver {
  constructor(callback: () => void) {
    observerCallback = callback;
  }
  observe() {}
  disconnect() {
    observerCallback = null;
  }
  unobserve() {}
}

function Queue() {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  useLeadTableFillHeight(wrapRef, true);
  return (
    <main>
      <div className="main__inner">
        <section className="surface">
          <div ref={wrapRef} className="tbl-wrap tbl-wrap--fill" />
        </section>
      </div>
    </main>
  );
}

/** One ResizeObserver delivery and the animation frame it schedules. */
function resizeAndFrame(): void {
  act(() => {
    observerCallback?.();
    for (const frame of frames.splice(0)) frame(0);
  });
}

describe('useLeadTableFillHeight', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    frames.length = 0;
    observerCallback = null;
    layout.mainHeight = 900;
    layout.printing = false;
    vi.stubGlobal('ResizeObserver', FakeResizeObserver);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      frames.push(callback);
      return frames.length;
    });
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation(() => undefined);
    vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      get matches() {
        return query === 'print' && layout.printing;
      },
      media: query,
      onchange: null,
      addEventListener: () => undefined,
      removeEventListener: () => undefined,
      addListener: () => undefined,
      removeListener: () => undefined,
      dispatchEvent: () => false,
    }) as MediaQueryList);
    // The scroller starts 300px into <main>, and its surface ends with it.
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function rect(this: HTMLElement) {
      const top = this.classList.contains('tbl-wrap') ? 300 : 0;
      const bottom = this.tagName === 'MAIN' ? 0 : 300;
      return { top, bottom, left: 0, right: 0, width: 0, height: 0, x: 0, y: top, toJSON: () => ({}) } as DOMRect;
    });
    vi.spyOn(HTMLElement.prototype, 'clientHeight', 'get').mockImplementation(function height(this: HTMLElement) {
      return this.tagName === 'MAIN' ? layout.mainHeight : 0;
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    act(() => root.render(<Queue />));
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
    vi.unstubAllGlobals();
  });

  const fill = () => container.querySelector<HTMLElement>('.tbl-wrap')!.style.getPropertyValue(LEAD_TABLE_FILL_VAR);

  it('sizes the scroller from its top edge to the fold of <main>, and follows <main> on the screen', () => {
    expect(fill()).toBe('600px');
    layout.mainHeight = 700;
    resizeAndFrame();
    expect(fill()).toBe('400px');
  });

  it('keeps the screen value through a print: the paper layout of <main> is not the fold', () => {
    expect(fill()).toBe('600px');
    // print.css unclips <main>: on paper it is as tall as the 600-row queue.
    layout.printing = true;
    layout.mainHeight = 27_200;
    resizeAndFrame();
    expect(fill()).toBe('600px');
    // Back on the screen, <main> resizes to the fold and the value holds.
    layout.printing = false;
    layout.mainHeight = 900;
    resizeAndFrame();
    expect(fill()).toBe('600px');
  });
});
