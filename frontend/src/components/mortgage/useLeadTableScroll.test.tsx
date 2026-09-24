// @vitest-environment happy-dom

import { act, useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter, Route, Routes, useNavigate, type NavigateFunction } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import {
  LEAD_TABLE_SCROLL_STORAGE_KEY,
  useLeadTableInitialOffset,
  useLeadTableScroll,
  type LeadTableVirtualScroll,
} from './useLeadTableScroll';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

/**
 * useLeadTableScroll — rendered-DOM contract for the Lead Queue's `.tbl-wrap`
 * scroller (audit runtime-08). The defect lived in the table's own scroller:
 * Back from a dossier opened the queue with the table at the top. These tests
 * mount a real router and a real scroller and assert its `scrollTop`.
 *
 * happy-dom has no layout, so the scroller's geometry is a prototype getter
 * that answers for `.tbl-wrap` only.
 */

let navigate: NavigateFunction;

function NavigateProbe() {
  const routerNavigate = useNavigate();
  useEffect(() => {
    navigate = routerNavigate;
  }, [routerNavigate]);
  return null;
}

/** The virtualizer stand-in a virtualized table passes, or null for a short table. */
const virtual: { current: LeadTableVirtualScroll | null } = { current: null };

function Table({ enabled }: { enabled: boolean }) {
  const wrapRef = useRef<HTMLDivElement | null>(null);
  const initialOffset = useLeadTableInitialOffset(enabled);
  useLeadTableScroll({ enabled, tableWrapRef: wrapRef, virtualizer: virtual.current });
  return (
    <div ref={wrapRef} className="tbl-wrap" data-initial-offset={initialOffset}>
      <table><tbody><tr><td>rows</td></tr></tbody></table>
    </div>
  );
}

const ROW = 'B-P5YP9ESW32R7Z';

describe('useLeadTableScroll', () => {
  let container: HTMLDivElement;
  let root: Root;
  let contentHeight: number;

  beforeEach(() => {
    window.sessionStorage.clear();
    contentHeight = 6000;
    virtual.current = null;
    Object.defineProperty(HTMLElement.prototype, 'scrollHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('tbl-wrap') ? contentHeight : 0;
      },
    });
    Object.defineProperty(HTMLElement.prototype, 'clientHeight', {
      configurable: true,
      get(this: HTMLElement) {
        return this.classList.contains('tbl-wrap') ? 520 : 0;
      },
    });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    window.sessionStorage.clear();
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).scrollHeight;
    delete (HTMLElement.prototype as unknown as Record<string, unknown>).clientHeight;
    vi.restoreAllMocks();
  });

  function wrap(): HTMLElement {
    const element = container.querySelector<HTMLElement>('.tbl-wrap');
    if (!element) throw new Error('the table is not mounted');
    return element;
  }

  async function mount(enabled = true): Promise<void> {
    await act(async () => {
      root.render(
        <MemoryRouter initialEntries={['/lead-queue']}>
          <NavigateProbe />
          <Routes>
            <Route path="/lead-queue" element={<Table enabled={enabled} />} />
            <Route path="/borrower-360/:id" element={<div>dossier</div>} />
          </Routes>
        </MemoryRouter>,
      );
    });
  }

  async function userScrollTo(offset: number): Promise<void> {
    await act(async () => {
      wrap().scrollTop = offset;
      wrap().dispatchEvent(new Event('scroll'));
    });
  }

  async function go(to: string | number, options?: { replace?: boolean }): Promise<void> {
    await act(async () => {
      if (typeof to === 'number') await navigate(to);
      else await navigate(to, options);
    });
  }

  /** Let MutationObserver callbacks run. */
  async function flushMutations(): Promise<void> {
    await act(async () => {
      await new Promise((resolve) => setTimeout(resolve, 0));
    });
  }

  it('Back from a dossier restores the offset the reader left the table at', async () => {
    await mount();
    await userScrollTo(1880);
    await go(`/borrower-360/${ROW}`);

    await go(-1);

    expect(wrap().scrollTop).toBe(1880);
  });

  it('creates a virtualized table at the saved offset, then scrolls it with scrollToOffset', async () => {
    await mount();
    await userScrollTo(2000);
    await go(`/borrower-360/${ROW}`);
    const scrollToOffset = vi.fn<(offset: number) => void>();
    virtual.current = { scrollToOffset, getTotalSize: () => 7040 };

    await go(-1);

    expect(wrap().dataset.initialOffset).toBe('2000');
    expect(scrollToOffset).toHaveBeenCalledWith(2000);
  });

  it('waits (bounded) until the rows are tall enough, then restores once', async () => {
    await mount();
    await userScrollTo(1880);
    await go(`/borrower-360/${ROW}`);
    contentHeight = 700;

    await go(-1);
    expect(wrap().scrollTop).toBe(0);

    contentHeight = 6000;
    await act(async () => {
      wrap().appendChild(document.createElement('span'));
    });
    await flushMutations();
    expect(wrap().scrollTop).toBe(1880);
  });

  it('a new entry (a sort, a filter, a preset) starts at the top; Back returns to the old offset', async () => {
    await mount();
    await userScrollTo(900);

    await go('/lead-queue?sort=equity&dir=desc');
    expect(wrap().scrollTop).toBe(0);

    await go(-1);
    expect(wrap().scrollTop).toBe(900);
  });

  it('expand / collapse (REPLACE) keeps the offset and carries it to the new entry', async () => {
    await mount();
    await userScrollTo(1320);

    await go(`/lead-queue?row=${ROW}`, { replace: true });
    expect(wrap().scrollTop).toBe(1320);

    await go(`/borrower-360/${ROW}`);
    await go(-1);
    expect(wrap().scrollTop).toBe(1320);
  });

  it('reader intent cancels a restore that is still waiting', async () => {
    await mount();
    await userScrollTo(1880);
    await go(`/borrower-360/${ROW}`);
    contentHeight = 700;
    await go(-1);

    await act(async () => {
      wrap().dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true }));
    });
    contentHeight = 6000;
    await act(async () => {
      wrap().appendChild(document.createElement('span'));
    });
    await flushMutations();

    expect(wrap().scrollTop).toBe(0);
  });

  it('stores numbers only, keyed by history entry: never a URL, never a borrower id', async () => {
    await mount();
    await userScrollTo(640);
    await go(`/lead-queue?row=${ROW}`, { replace: true });
    await userScrollTo(700);
    await go(`/borrower-360/${ROW}`);

    const raw = window.sessionStorage.getItem(LEAD_TABLE_SCROLL_STORAGE_KEY) ?? '';
    expect(raw).not.toBe('');
    expect(raw).not.toContain('B-');
    expect(raw).not.toContain('lead-queue');
    expect(raw).not.toContain('row');
    const entries = JSON.parse(raw) as unknown[];
    expect(entries.length).toBeGreaterThan(0);
    for (const entry of entries) {
      expect(Array.isArray(entry)).toBe(true);
      const [key, value] = entry as unknown[];
      expect(typeof key).toBe('string');
      expect(typeof value).toBe('number');
    }
    expect(entries.map((entry) => (entry as [string, number])[1])).toContain(700);
  });

  it('does nothing unless enabled (Segment Intelligence keeps today\'s table)', async () => {
    await mount(false);
    await userScrollTo(900);
    await go(`/borrower-360/${ROW}`);
    await go(-1);

    expect(wrap().scrollTop).toBe(0);
    expect(window.sessionStorage.getItem(LEAD_TABLE_SCROLL_STORAGE_KEY)).toBeNull();
  });
});
