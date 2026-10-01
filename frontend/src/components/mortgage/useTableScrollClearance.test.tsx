/**
 * @vitest-environment happy-dom
 *
 * useTableScrollClearance (audit a11y-v2): measures the sticky thead and
 * writes it as the custom property the clearance rules in LeadTable.css
 * read. Every measurement, the first one included, is taken in the next
 * animation frame: never during the commit (a synchronous layout before the
 * first paint) and never inside the ResizeObserver callback. It is removed
 * on unmount. The pinned Approval column is not measured: its size is its
 * column's declared width (LeadTable.css). The sticky route nav is 38-focus-clearance.css's (its
 * one-line size, on every route): nothing here writes to `.main`. The
 * rendered proof (no focus stop under the chrome) is the focus-obscured
 * walk in lead-queue.fixture.spec.ts.
 *
 * The pinned-focus guard (WebKit 26, manual check 2026-09-30): a focus on a
 * control in the pinned Approval column must not leave the table scrolled
 * to its end. The engine's scroll and its scroll events are emulated here
 * (happy-dom has no layout), on either side of the focus events; the
 * rendered proof in both engines is
 * approval-core-pinned-focus.cross-engine.fixture.spec.ts, and the
 * lead-queue.fixture.spec.ts ZERO_PIN_MARGIN twin in Chromium.
 */
import { act, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { TABLE_HEAD_BLOCK_VAR, useTableScrollClearance } from './useTableScrollClearance';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const sizes = { head: 44.4, pin: 156 };
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

function PinnedTable() {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  useTableScrollClearance(wrapRef, 'default');
  return (
    <div ref={wrapRef} className="tbl-wrap">
      <table className="lead-table__table">
        <thead>
          <tr>
            <th>Borrower</th>
            <th className="lead-table__approval-header">Approval</th>
          </tr>
        </thead>
        <tbody>
          <tr>
            <td><button type="button" data-testid="borrower">B-0000000000001</button></td>
            <td className="tbl-cell--approval"><button type="button" data-testid="approve">Approve</button></td>
          </tr>
        </tbody>
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

  it('writes the thead size in the frame after mount (rounded up to whole pixels), not during the commit, and nothing else', () => {
    act(() => root.render(<Table />));
    expect(wrap().style.getPropertyValue(TABLE_HEAD_BLOCK_VAR), 'no layout read during the commit').toBe('');
    expect(frames).toHaveLength(1);
    act(() => frames.shift()?.(0));
    expect(wrap().style.getPropertyValue(TABLE_HEAD_BLOCK_VAR)).toBe('45px');
    expect(wrap().style.getPropertyValue('--tbl-pin-inline-size'), 'the pin is its declared column width').toBe('');
    expect(observed.map((element) => element.tagName.toLowerCase())).toEqual(['thead']);
    expect(main.getAttribute('style'), 'the route nav is 38-focus-clearance.css\'s').toBeNull();
  });

  it('writes a resize in the next animation frame, never inside the observer callback', () => {
    act(() => root.render(<Table />));
    act(() => frames.shift()?.(0));
    sizes.head = 88;
    act(() => observerCallback?.());
    expect(wrap().style.getPropertyValue(TABLE_HEAD_BLOCK_VAR), 'unchanged inside the callback').toBe('45px');
    expect(frames).toHaveLength(1);
    act(() => observerCallback?.());
    expect(frames, 'one frame per burst').toHaveLength(1);

    act(() => frames.shift()?.(0));

    expect(wrap().style.getPropertyValue(TABLE_HEAD_BLOCK_VAR)).toBe('88px');
  });

  it('removes the property on unmount, and a measurement still pending never lands', () => {
    act(() => root.render(<Table />));
    act(() => frames.shift()?.(0));
    const table = wrap();
    act(() => root.render(<div />));
    expect(table.style.getPropertyValue(TABLE_HEAD_BLOCK_VAR)).toBe('');
  });

  describe('the pinned-focus guard', () => {
    const button = (id: string) => document.querySelector<HTMLButtonElement>(`[data-testid="${id}"]`)!;
    async function flushMicrotasks() {
      await act(async () => {
        await Promise.resolve();
      });
    }
    function mountPinned() {
      act(() => root.render(<PinnedTable />));
      // The thead measurement's frame.
      act(() => frames.shift()?.(0));
    }

    it('restores the offset a programmatic focus() scrolled away, in a microtask', async () => {
      mountPinned();
      wrap().scrollLeft = 0;
      act(() => {
        button('approve').focus();
        // The engine's reveal scroll, after the focus events (WebKit 26).
        wrap().scrollLeft = 284;
      });
      expect(wrap().scrollLeft, 'the engine scrolled').toBe(284);
      await flushMicrotasks();
      expect(wrap().scrollLeft).toBe(0);
      act(() => frames.shift()?.(0));
      expect(wrap().scrollLeft, 'the frame finds nothing left to restore').toBe(0);
    });

    it('restores a Tab or click focus, whose scroll lands after the microtask, in the next frame', async () => {
      mountPinned();
      // The reader's own scroll, recorded by its scroll event.
      wrap().scrollLeft = 120;
      wrap().dispatchEvent(new Event('scroll'));
      act(() => {
        button('approve').dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
      });
      await flushMicrotasks();
      wrap().scrollLeft = 284;
      expect(frames).toHaveLength(1);
      act(() => frames.shift()?.(0));
      expect(wrap().scrollLeft).toBe(120);
    });

    it('after a pointer press, a scroll made before the focus events is the reveal: the press offset wins', async () => {
      mountPinned();
      wrap().scrollLeft = 100;
      act(() => {
        button('approve').dispatchEvent(new PointerEvent('pointerdown', { bubbles: true }));
        wrap().scrollLeft = 284;
        button('approve').dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
      });
      await flushMicrotasks();
      expect(wrap().scrollLeft).toBe(100);
    });

    it('an engine that scrolls before the focus events (Chromium without the margin): the recorded offset wins', async () => {
      mountPinned();
      wrap().scrollLeft = 40;
      wrap().dispatchEvent(new Event('scroll'));
      act(() => {
        // The reveal lands first; its scroll event would come a frame later.
        wrap().scrollLeft = 284;
        button('approve').dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
      });
      expect(wrap().scrollLeft, 'the engine scrolled').toBe(284);
      await flushMicrotasks();
      expect(wrap().scrollLeft).toBe(40);
    });

    it('keeps the engine scroll when the control would not be fully in view at the restored offset', async () => {
      mountPinned();
      vi.mocked(HTMLElement.prototype.getBoundingClientRect).mockImplementation(function rect(this: HTMLElement) {
        const left = this.dataset.testid === 'approve' && wrap().scrollLeft === 0 ? 900 : 0;
        return { width: 10, height: 10, top: 0, left, right: left + 10, bottom: 10, x: left, y: 0, toJSON: () => ({}) } as DOMRect;
      });
      Object.defineProperty(wrap(), 'clientWidth', { configurable: true, value: 400 });
      wrap().scrollLeft = 0;
      act(() => {
        button('approve').focus();
        wrap().scrollLeft = 284;
      });
      await flushMicrotasks();
      expect(wrap().scrollLeft, 'not pinned in view there: the reveal stands').toBe(284);
    });

    it('ignores a focus outside the pinned column', async () => {
      mountPinned();
      wrap().scrollLeft = 0;
      act(() => {
        button('borrower').focus();
        wrap().scrollLeft = 64;
      });
      await flushMicrotasks();
      expect(frames).toHaveLength(0);
      expect(wrap().scrollLeft).toBe(64);
    });

    it('removes its listeners with the table', async () => {
      mountPinned();
      const table = wrap();
      const approve = button('approve');
      act(() => root.render(<div />));
      table.scrollLeft = 0;
      approve.dispatchEvent(new FocusEvent('focusin', { bubbles: true }));
      table.scrollLeft = 284;
      await flushMicrotasks();
      expect(table.scrollLeft).toBe(284);
      expect(frames).toHaveLength(0);
    });
  });
});
