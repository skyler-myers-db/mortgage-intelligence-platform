/**
 * @vitest-environment happy-dom
 *
 * Reveal a restored `?row=` (audit tables-09 follow-up; wave-3 queue-place
 * item 6c). The Lead Queue keeps the expanded row in the URL, but the table
 * scroller starts at the top on a load or a PUSH, and on a virtualized queue
 * (> 120 rows) a row far down is not even rendered: the reader landed on an
 * "expanded" row they could not see. Now useLeadTableScroll's fresh-entry
 * seam (a PUSH, or a first load with nothing saved) asks the table to
 * reveal the named row through the cursor (the virtualizer's scrollToIndex,
 * then scrollIntoView), once that row is among the rows on screen. Never on
 * REPLACE (an expand or a collapse).
 *
 * Rendered LeadTable with the real hooks; the virtualizer's scrollToIndex
 * and scrollToOffset are observed through a pass-through mock. A POP to an
 * entry with a saved offset restores that offset (useLeadTableScroll runs in
 * the shell, which owns the scroller: run from LeadTableBody, its first
 * layout effect found no `.tbl-wrap` and the restore never ran) and reveals
 * nothing. The e2e with real layout is lead-queue.fixture.spec.ts (f).
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { createMemoryRouter, useSearchParams } from 'react-router';
import { RouterProvider } from 'react-router/dom';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';
import { parseLeadTablePlace, searchParamsWithLeadTablePlace } from '../../routes/lead-queue.filters';
import { scrollStorageKey, writeOffsets } from '../../hooks/scrollOffsetStore';
import { LEAD_TABLE_SCROLL_STORAGE_KEY } from './useLeadTableScroll';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const scrolledTo = vi.hoisted(() => [] as number[]);
const offsetsSet = vi.hoisted(() => [] as number[]);

vi.mock('@tanstack/react-virtual', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-virtual')>();
  const patched = new WeakSet<object>();
  return {
    ...actual,
    useVirtualizer: ((options: Parameters<typeof actual.useVirtualizer>[0]) => {
      const instance = actual.useVirtualizer(options);
      if (!patched.has(instance)) {
        patched.add(instance);
        const original = instance.scrollToIndex.bind(instance);
        instance.scrollToIndex = (index, scrollOptions) => {
          scrolledTo.push(index);
          original(index, scrollOptions);
        };
        const originalOffset = instance.scrollToOffset.bind(instance);
        instance.scrollToOffset = (offset, scrollOptions) => {
          offsetsSet.push(offset);
          originalOffset(offset, scrollOptions);
        };
      }
      return instance;
    }) as typeof actual.useVirtualizer,
  };
});

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    setLastBorrowerId: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
    density: 'comfortable',
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: vi.fn(),
    approve: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
}));

import { LeadTable } from './LeadTable';

const IDS = Array.from({ length: 160 }, (_, index) => `B-REVEAL${String(index).padStart(7, '0')}`);

function lead(borrowerId: string, index: number): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 99 - Math.floor(index / 2),
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

const LEADS = IDS.map(lead);

/** lead-queue.tsx's place wiring: the row lives in `?row=`; expand and collapse replace. */
function PlaceQueue() {
  const [searchParams, setSearchParams] = useSearchParams();
  const place = parseLeadTablePlace(searchParams);
  return (
    <LeadTable
      leads={LEADS}
      restoreScroll
      expandedId={place.row}
      onExpandedChange={(borrowerId) => setSearchParams(
        searchParamsWithLeadTablePlace(searchParams, { row: borrowerId }),
        { replace: true },
      )}
    />
  );
}

describe('LeadTable reveals the row the URL names', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    installLocalStorage();
    window.sessionStorage.clear();
    clearSingleKeyShortcutsPreference();
    scrolledTo.length = 0;
    offsetsSet.length = 0;
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.restoreAllMocks();
  });

  function mount(initialEntry: string) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    const router = createMemoryRouter([{ path: '/lead-queue', element: <PlaceQueue /> }], {
      initialEntries: [initialEntry],
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <RouterProvider router={router} />
        </QueryClientProvider>,
      );
    });
    return router;
  }

  async function flush() {
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 0));
    });
  }

  it('on load, scrolls the virtualized row 140 into the window', async () => {
    mount(`/lead-queue?row=${IDS[140]}`);
    await flush();
    expect(scrolledTo).toEqual([140]);
  });

  it('on a PUSH naming another loaded row, reveals that row; a load with no row reveals nothing', async () => {
    const router = mount('/lead-queue');
    await flush();
    expect(scrolledTo, 'no ?row=: nothing to reveal').toEqual([]);

    await act(async () => {
      await router.navigate(`/lead-queue?approval_status=pending&row=${IDS[150]}`);
    });
    await flush();

    expect(scrolledTo).toEqual([150]);
  });

  it('a POP to an entry with a saved offset restores it on the first mount, and reveals nothing', async () => {
    const search = `?row=${IDS[140]}`;
    const key = scrollStorageKey({ key: 'default', pathname: '/lead-queue', search, hash: '' });
    writeOffsets(LEAD_TABLE_SCROLL_STORAGE_KEY, new Map([[key, 4_000]]));

    mount(`/lead-queue${search}`);

    // No layout here, so the rows never grow tall enough: the restore goes
    // as far as it can at its deadline, through the virtualizer.
    await vi.waitFor(async () => {
      await flush();
      expect(offsetsSet).toContain(4_000);
    }, { timeout: 10_000 });
    expect(scrolledTo, 'a restored place is not overridden by a reveal').toEqual([]);
  }, 30_000);

  it('an unmount before the reveal\'s frame cancels that frame: nothing scrolls afterwards', async () => {
    const frames: number[] = [];
    const cancelled: number[] = [];
    const request = window.requestAnimationFrame.bind(window);
    vi.spyOn(window, 'requestAnimationFrame').mockImplementation((callback) => {
      const id = request(callback);
      frames.push(id);
      return id;
    });
    const cancel = window.cancelAnimationFrame.bind(window);
    vi.spyOn(window, 'cancelAnimationFrame').mockImplementation((id) => {
      cancelled.push(id);
      cancel(id);
    });
    mount(`/lead-queue?row=${IDS[140]}`);
    expect(frames.length, 'precondition: the reveal waits for its frame').toBeGreaterThan(0);

    act(() => root.unmount());
    root = createRoot(container);
    await act(async () => {
      await new Promise((resolve) => window.setTimeout(resolve, 50));
    });

    expect(cancelled.some((id) => frames.includes(id)), 'the pending frame was cancelled').toBe(true);
    expect(scrolledTo, 'no reveal ran after the unmount').toEqual([]);
  });

  it('never on REPLACE: an expand or a collapse keeps the reader\'s place', async () => {
    const router = mount(`/lead-queue?row=${IDS[140]}`);
    await flush();
    scrolledTo.length = 0;

    await act(async () => {
      await router.navigate(`/lead-queue?row=${IDS[141]}`, { replace: true });
    });
    await flush();

    expect(scrolledTo).toEqual([]);
  });
});
