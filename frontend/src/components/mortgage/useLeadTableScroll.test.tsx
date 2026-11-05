// @vitest-environment happy-dom

import { act, useEffect, useRef } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import {
  MemoryRouter,
  Route,
  Routes,
  useLocation,
  useNavigate,
  type Location,
  type NavigateFunction,
} from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { fingerprint, type ScrollLocation } from '../../hooks/scrollOffsetStore';
import {
  LEAD_TABLE_SCROLL_STORAGE_KEY,
  useLeadTableInitialOffset,
  useLeadTableScroll,
  type LeadTableVirtualScroll,
} from './useLeadTableScroll';

/**
 * useLeadTableScroll — rendered-DOM contract for the Lead Queue's `.tbl-wrap`
 * scroller (audit runtime-08). The defect lived in the table's own scroller:
 * Back from a dossier opened the queue with the table at the top. These tests
 * mount a real router and a real scroller and assert its `scrollTop`.
 *
 * happy-dom has no layout, so the scroller's geometry is a prototype getter
 * that answers for `.tbl-wrap` only.
 */

/** The control below makes the hook key its offsets by URL, as a regression would. */
const leak = vi.hoisted(() => ({ urlKeys: false }));

vi.mock('../../hooks/scrollOffsetStore', async (importOriginal) => {
  const real = await importOriginal<typeof import('../../hooks/scrollOffsetStore')>();
  return {
    ...real,
    scrollStorageKey: (location: ScrollLocation) => (
      leak.urlKeys ? `${location.pathname}${location.search}` : real.scrollStorageKey(location)
    ),
  };
});

let navigate: NavigateFunction;
let currentLocation: Location;

function RouterProbe() {
  const routerNavigate = useNavigate();
  const routerLocation = useLocation();
  useEffect(() => {
    navigate = routerNavigate;
    currentLocation = routerLocation;
  }, [routerNavigate, routerLocation]);
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

/**
 * The two shapes a stored key may take. A pushed or replaced entry: React
 * Router's createKey(), `Math.random().toString(36).substring(2, 10)`. The
 * first load: scrollStorageKey's "default:" plus an unsigned 32-bit FNV-1a
 * hash in base36 (at most 7 digits).
 */
const ROUTER_ENTRY_KEY = /^[0-9a-z]{1,8}$/;
const FIRST_LOAD_KEY = /^default:[0-9a-z]{1,7}$/;

/** What a URL or a borrower id would bring into storage. */
const NEVER_STORED = [ROW, 'B-', '/lead-queue', 'lead-queue', '?row=', `row=${ROW}`, '/borrower-360', 'borrower-360'];

/** The table's first-load entry key: the fingerprint of '/lead-queue', not the URL. */
const FIRST_LOAD_ENTRY = `default:${fingerprint('/lead-queue')}`;

/**
 * The router key minted in main CI on 2026-11-04 (run 41867598276): random
 * base36 that spells "row", which a substring scan read as a leak.
 */
const COLLIDING_ENTRY_KEY = '9awbrowk';

/**
 * The stored offsets, checked by shape rather than by substrings of random
 * keys: a JSON array of [key, finite number] pairs whose keys are router
 * entry keys or the first-load fingerprint, with no URL or borrower id
 * anywhere in the raw value. Every NEVER_STORED fragment holds a character no
 * key can, so a random key never spells one.
 */
function storedEntries(): Array<[string, number]> {
  const raw = window.sessionStorage.getItem(LEAD_TABLE_SCROLL_STORAGE_KEY);
  if (raw === null) throw new Error('nothing was stored');
  for (const fragment of NEVER_STORED) {
    expect(fragment).toMatch(/[^0-9a-z:]/);
    expect(raw).not.toContain(fragment);
  }
  const entries: unknown = JSON.parse(raw);
  if (!Array.isArray(entries)) throw new Error(`not an array: ${raw}`);
  for (const entry of entries) {
    expect(Array.isArray(entry) && entry.length === 2, `entry ${JSON.stringify(entry)}`).toBe(true);
    const [key, value] = entry as [unknown, unknown];
    expect(typeof key === 'string' && (ROUTER_ENTRY_KEY.test(key) || FIRST_LOAD_KEY.test(key)), `key ${String(key)}`)
      .toBe(true);
    expect(Number.isFinite(value), `offset ${String(value)}`).toBe(true);
  }
  return entries as Array<[string, number]>;
}

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
          <RouterProbe />
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

  // Seen in a real browser: a virtualized table's first commit holds only
  // its header, so an offset the virtualizer's estimate says is reachable is
  // clamped to 0 by the scroller. The restore waits for the rendered rows.
  it('waits for the rendered rows even when the virtualizer estimate says the offset is reachable', async () => {
    await mount();
    await userScrollTo(2000);
    await go(`/borrower-360/${ROW}`);
    const scrollToOffset = vi.fn<(offset: number) => void>();
    virtual.current = { scrollToOffset, getTotalSize: () => 7040 };
    contentHeight = 57;

    await go(-1);
    expect(scrollToOffset).not.toHaveBeenCalled();

    contentHeight = 7100;
    await act(async () => {
      wrap().appendChild(document.createElement('span'));
    });
    await flushMutations();
    expect(scrollToOffset).toHaveBeenCalledWith(2000);
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
    const expanded = currentLocation;
    await userScrollTo(700);
    await go(`/borrower-360/${ROW}`);

    expect(storedEntries()).toEqual([[FIRST_LOAD_ENTRY, 640], [expanded.key, 700]]);
  });

  it('a router key that happens to spell "row" is not a leak', async () => {
    await mount();
    await userScrollTo(640);
    // createKey() is Math.random().toString(36).substring(2, 10): mint the CI key.
    const random = vi.spyOn(Math, 'random').mockReturnValue((Number.parseInt(COLLIDING_ENTRY_KEY, 36) + 0.5) / 36 ** 8);
    await go(`/lead-queue?row=${ROW}`, { replace: true });
    random.mockRestore();
    expect(currentLocation.key).toBe(COLLIDING_ENTRY_KEY);
    await userScrollTo(700);
    await go(`/borrower-360/${ROW}`);

    // The collision is real: the raw value holds "row", but only inside the key.
    expect(window.sessionStorage.getItem(LEAD_TABLE_SCROLL_STORAGE_KEY)).toContain('row');
    expect(storedEntries()).toEqual([[FIRST_LOAD_ENTRY, 640], [COLLIDING_ENTRY_KEY, 700]]);
  });

  it('the shape check still catches offsets keyed by URL', async () => {
    // Control: a hook that keyed its offsets by URL is caught through the real
    // storage write, not only by a crafted value.
    leak.urlKeys = true;
    try {
      await mount();
      await userScrollTo(640);
      await go(`/lead-queue?row=${ROW}`, { replace: true });
      await userScrollTo(700);
      await go(`/borrower-360/${ROW}`);
    } finally {
      leak.urlKeys = false;
    }

    expect(window.sessionStorage.getItem(LEAD_TABLE_SCROLL_STORAGE_KEY)).toContain(`/lead-queue?row=${ROW}`);
    expect(() => storedEntries()).toThrow(/lead-queue/);
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
