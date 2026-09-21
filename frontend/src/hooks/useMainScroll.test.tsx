// @vitest-environment happy-dom

import { act, useEffect, useRef, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  MAIN_SCROLL_STORAGE_KEY,
  readStoredOffsets,
  rememberOffset,
  scrollStorageKey,
  useMainScroll,
} from './useMainScroll';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * useMainScroll — rendered-DOM contract for the persistent `.main` scroller.
 *
 * The defect lived in the shell: `<main className="main">` never remounts, so
 * its `scrollTop` leaked across routes (audit 2026-09-21: Home 689 → Analytics
 * opened at 570; Back to the Lead Queue returned 0, not 770; `/glossary#clip`
 * never scrolled). These tests mount a real router + a real `<main>` and assert
 * the element's `scrollTop`, which is the layer the defect was observed at.
 *
 * happy-dom has no layout engine, so `scrollHeight` / `clientHeight` and the
 * bounding rects are stubbed per test. `scrollTop` itself is a real settable
 * property. A follow-up Playwright lane re-proves the same behaviours against
 * real layout.
 */

let navigate: NavigateFunction;

function NavigateProbe() {
  const routerNavigate = useNavigate();
  useEffect(() => {
    navigate = routerNavigate;
  }, [routerNavigate]);
  return null;
}

function Shell({ children }: { children: ReactNode }) {
  const mainRef = useRef<HTMLElement | null>(null);
  useMainScroll(mainRef);
  return (
    <main ref={mainRef} id="main-content" className="main">
      <nav className="route-nav" />
      {children}
    </main>
  );
}

function GlossaryPage({ lazyTerm }: { lazyTerm: boolean }) {
  return (
    <div>
      <article id="avm" style={{ scrollMarginTop: 64 }}>AVM</article>
      {lazyTerm ? null : <article id="clip" style={{ scrollMarginTop: 64 }}>CLIP</article>}
    </div>
  );
}

/** Viewport-relative boxes, keyed by element id or class (no layout engine). */
const rects = new Map<string, { top: number; height: number }>();

type Entry = string | { pathname: string; key?: string; hash?: string; search?: string };

describe('useMainScroll', () => {
  let container: HTMLDivElement;
  let root: Root;
  let contentHeight: number;

  beforeEach(() => {
    window.sessionStorage.clear();
    contentHeight = 4000;
    rects.clear();
    vi.spyOn(HTMLElement.prototype, 'getBoundingClientRect').mockImplementation(function rect(
      this: HTMLElement,
    ) {
      const box = rects.get(this.id) ?? rects.get(this.className) ?? { top: 0, height: 0 };
      return { ...box, bottom: box.top + box.height, left: 0, right: 0, width: 0, x: 0, y: box.top } as DOMRect;
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.sessionStorage.clear();
    vi.restoreAllMocks();
  });

  function main(): HTMLElement {
    return container.querySelector('main') as HTMLElement;
  }

  /** Give the scroller a geometry: 800px viewport over `contentHeight` of content. */
  function stubGeometry(): void {
    const el = main();
    Object.defineProperty(el, 'scrollHeight', { configurable: true, get: () => contentHeight });
    Object.defineProperty(el, 'clientHeight', { configurable: true, get: () => 800 });
  }

  async function mount(initialEntries: Entry[], opts: { lazyTerm?: boolean } = {}): Promise<void> {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={initialEntries}>
          <NavigateProbe />
          <Shell>
            <Routes>
              <Route path="/home" element={<div>home</div>} />
              <Route path="/analytics" element={<div>analytics</div>} />
              <Route path="/lead-queue" element={<div>queue</div>} />
              <Route path="/borrower-360/:id" element={<div>borrower</div>} />
              <Route path="/glossary" element={<GlossaryPage lazyTerm={opts.lazyTerm ?? false} />} />
            </Routes>
          </Shell>
        </MemoryRouter>,
      );
    });
    stubGeometry();
  }

  /** What a user scroll does: move the offset, then the browser fires `scroll`. */
  async function userScrollTo(offset: number): Promise<void> {
    await act(async () => {
      main().scrollTop = offset;
      main().dispatchEvent(new Event('scroll'));
    });
  }

  async function go(to: string | number, options?: { replace?: boolean }): Promise<void> {
    await act(async () => {
      if (typeof to === 'number') await navigate(to);
      else await navigate(to, options);
    });
  }

  /** Let MutationObserver callbacks (microtask-delivered) run. */
  async function flushMutations(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('PUSH to a different route opens at the top instead of inheriting the old offset', async () => {
    await mount(['/home']);
    await userScrollTo(689);

    await go('/analytics');

    expect(main().scrollTop).toBe(0);
  });

  it('REPLACE to a different route also opens at the top', async () => {
    await mount(['/home']);
    await userScrollTo(412);

    await go('/lead-queue', { replace: true });

    expect(main().scrollTop).toBe(0);
  });

  it('Back restores the offset the user left the queue at', async () => {
    await mount(['/lead-queue']);
    await userScrollTo(770);
    await go('/borrower-360/B-0123456789ABC');
    expect(main().scrollTop).toBe(0);

    await go(-1);

    expect(main().scrollTop).toBe(770);
  });

  it('Forward restores too, and each history entry keeps its own offset', async () => {
    await mount(['/lead-queue']);
    await userScrollTo(770);
    await go('/analytics');
    await userScrollTo(300);

    await go(-1);
    expect(main().scrollTop).toBe(770);
    await go(1);
    expect(main().scrollTop).toBe(300);
  });

  it('waits for content to be tall enough before restoring, then restores once', async () => {
    await mount(['/lead-queue']);
    await userScrollTo(770);
    await go('/analytics');

    // Back lands while the queue is still a short skeleton: 770 is unreachable.
    contentHeight = 900;
    await go(-1);
    expect(main().scrollTop).toBe(0);

    // Rows arrive: the DOM grows, and the pending restore completes.
    contentHeight = 4000;
    await act(async () => {
      main().appendChild(document.createElement('section'));
    });
    await flushMutations();
    expect(main().scrollTop).toBe(770);
  });

  it('does not fight the user: scroll intent during a pending restore cancels it', async () => {
    await mount(['/lead-queue']);
    await userScrollTo(770);
    await go('/analytics');
    contentHeight = 900;
    await go(-1);

    await act(async () => {
      main().dispatchEvent(new Event('wheel'));
      main().scrollTop = 40;
      main().dispatchEvent(new Event('scroll'));
    });
    contentHeight = 4000;
    await act(async () => {
      main().appendChild(document.createElement('section'));
    });
    await flushMutations();

    expect(main().scrollTop).toBe(40);
  });

  it('keeps the offset when only the search params change (URL-synced filters)', async () => {
    await mount(['/analytics']);
    await userScrollTo(500);

    await go('/analytics?states=TX');
    expect(main().scrollTop).toBe(500);

    // ...and Back through the filter history returns to where that entry was.
    await userScrollTo(900);
    await go(-1);
    expect(main().scrollTop).toBe(500);
  });

  it('scrolls a #hash target under the sticky nav on client navigation', async () => {
    await mount(['/home']);
    await userScrollTo(250);
    rects.set('main-content', { top: 100, height: 800 });
    rects.set('route-nav', { top: 100, height: 53 });
    rects.set('clip', { top: 1500, height: 120 });

    await go('/glossary#clip');

    // New route, so it starts from 0; then (1500 - 100) to the target, minus
    // the larger of the CSS scroll-margin-top (64) and the sticky nav (53).
    expect(main().scrollTop).toBe(1336);
  });

  it('clears a wrapped (two-row) sticky nav even when it is taller than the CSS margin', async () => {
    await mount(['/home']);
    rects.set('main-content', { top: 100, height: 800 });
    rects.set('route-nav', { top: 100, height: 96 });
    rects.set('clip', { top: 1500, height: 120 });

    await go('/glossary#clip');

    expect(main().scrollTop).toBe(1400 - 96);
  });

  it('places the target by layout offset, so the route entrance transform cannot skew it', async () => {
    await mount(['/home']);
    rects.set('route-nav', { top: 100, height: 53 });
    // Mid-animation the rect says 1504 (translateY(4px)); the layout offset says 1400.
    rects.set('clip', { top: 1504, height: 120 });
    rects.set('main-content', { top: 100, height: 800 });
    const offsetTop = vi.spyOn(HTMLElement.prototype, 'offsetTop', 'get').mockImplementation(function top(
      this: HTMLElement,
    ) {
      return this.id === 'clip' ? 1400 : 0;
    });
    // happy-dom does not implement offsetParent at all; define it for this test.
    Object.defineProperty(HTMLElement.prototype, 'offsetParent', {
      configurable: true,
      get(this: HTMLElement) {
        return this.id === 'clip' ? main() : null;
      },
    });

    try {
      await go('/glossary#clip');
      expect(main().scrollTop).toBe(1400 - 64);
    } finally {
      offsetTop.mockRestore();
      Reflect.deleteProperty(HTMLElement.prototype, 'offsetParent');
    }
  });

  it('a hash change on the same page jumps to the new target', async () => {
    await mount(['/glossary']);
    await userScrollTo(300);
    rects.set('avm', { top: 900, height: 120 });

    await go('/glossary#avm');

    // 300 (current) + 900 (distance below the scroller top) - 64.
    expect(main().scrollTop).toBe(1136);
  });

  it('scrolls to a #hash target that renders late (lazy route)', async () => {
    await mount(['/home'], { lazyTerm: true });
    rects.set('clip', { top: 640, height: 120 });

    await go('/glossary#clip');
    expect(main().scrollTop).toBe(0);

    await act(async () => {
      const term = document.createElement('article');
      term.id = 'clip';
      main().appendChild(term);
    });
    await flushMutations();

    expect(main().scrollTop).toBe(640);
  });

  it('leaves targets outside <main> (the skip links) to the browser', async () => {
    await mount(['/lead-queue']);
    await userScrollTo(520);

    await go('/lead-queue#main-content');

    expect(main().scrollTop).toBe(520);
  });

  it('mirrors offsets to sessionStorage so a reload restores the entry', async () => {
    await mount([{ pathname: '/lead-queue', key: 'entry-1' }]);
    await userScrollTo(640);
    await act(async () => {
      window.dispatchEvent(new Event('pagehide'));
    });
    expect(readStoredOffsets().get('entry-1')).toBe(640);

    // Reload: a fresh document, same history entry key, scroller back at 0.
    act(() => root.unmount());
    root = createRoot(container);
    await mount([{ pathname: '/lead-queue', key: 'entry-1' }]);
    // The restore was pending on geometry (stubbed after mount): grow the DOM.
    await act(async () => {
      main().appendChild(document.createElement('section'));
    });
    await flushMutations();

    expect(main().scrollTop).toBe(640);
  });
});

describe('scroll offset store', () => {
  beforeEach(() => window.sessionStorage.clear());

  it('is bounded: the least recently used entry is evicted first', () => {
    const offsets = new Map<string, number>();
    for (let i = 0; i < 60; i += 1) rememberOffset(offsets, `k${i}`, i);
    expect(offsets.size).toBe(50);
    expect(offsets.has('k9')).toBe(false);
    expect(offsets.get('k59')).toBe(59);

    rememberOffset(offsets, 'k10', 1010); // touch the oldest survivor
    rememberOffset(offsets, 'new', 1);
    expect(offsets.get('k10')).toBe(1010);
    expect(offsets.has('k11')).toBe(false);
  });

  it('ignores a corrupt or hostile stored value', () => {
    window.sessionStorage.setItem(MAIN_SCROLL_STORAGE_KEY, '{"not":"an array"}');
    expect(readStoredOffsets().size).toBe(0);
    window.sessionStorage.setItem(MAIN_SCROLL_STORAGE_KEY, '[["ok",12],["bad","x"],[3,4],"junk"]');
    expect([...readStoredOffsets()]).toEqual([['ok', 12]]);
  });

  it('tells first-load entries apart by URL without storing the URL', () => {
    const queue = scrollStorageKey({ key: 'default', pathname: '/lead-queue', search: '', hash: '' });
    const skip = scrollStorageKey({ key: 'default', pathname: '/lead-queue', search: '', hash: '#main-content' });
    const borrower = scrollStorageKey({
      key: 'default',
      pathname: '/borrower-360/B-0123456789ABC',
      search: '',
      hash: '',
    });
    expect(queue).not.toBe(skip);
    expect(borrower).not.toContain('B-0123456789ABC');
    expect(borrower).toMatch(/^default:[0-9a-z]+$/);
    // Router-created entries already carry a unique random key.
    expect(scrollStorageKey({ key: 'a1b2c3d4', pathname: '/x', search: '', hash: '' })).toBe('a1b2c3d4');
  });
});
