/**
 * @vitest-environment happy-dom
 *
 * responsive-08 (W5e): a print of the virtualized Lead Queue carries every
 * LOADED row. `beforeprint` opens the virtual window synchronously (no read,
 * no refetch, no audit row); `afterprint` re-windows and restores the table
 * scroller's offset through the virtualizer. Proven on the rendered table
 * with the real virtualizer (LeadTableBody), the layer where the window
 * lived.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';

const api = vi.hoisted(() => ({
  draftOutreach: vi.fn(),
  approve: vi.fn(),
  reject: vi.fn(),
  campaign: vi.fn(),
  auditReceipt: vi.fn(() => new Promise(() => {})),
  salesTeam: vi.fn(() => Promise.resolve({ members: [] })),
}));
const scrollToOffset = vi.hoisted(() => [] as number[]);

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: () => undefined,
    setLastBorrowerId: () => undefined,
    openConsoleRecentActivity: () => undefined,
    setDrawer: () => undefined,
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
  api,
}));

// Counts the restore through the virtualizer the body publishes.
vi.mock('@tanstack/react-virtual', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-virtual')>();
  return {
    ...actual,
    useVirtualizer: ((options: Parameters<typeof actual.useVirtualizer>[0]) => {
      const virtualizer = actual.useVirtualizer(options);
      const original = virtualizer.scrollToOffset.bind(virtualizer);
      virtualizer.scrollToOffset = (offset, behavior) => {
        scrollToOffset.push(offset);
        original(offset, behavior);
      };
      return virtualizer;
    }) as typeof actual.useVirtualizer,
  };
});

import { LeadTable } from './LeadTable';

const ROWS = 600;

function lead(index: number): LeadSummary {
  return {
    borrower_id: `B-PRINT${String(index).padStart(8, '0')}`,
    clip: `clip_print_${index}`,
    display_name: `Owner ${index}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000,
    rate_spread_bps: 120,
    opportunity_score: 90,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

const LEADS = Array.from({ length: ROWS }, (_, index) => lead(index));

const rowCount = (container: HTMLElement) => container.querySelectorAll('tr[data-borrower-row]').length;
const apiCalls = () => Object.values(api).reduce((sum, fn) => sum + fn.mock.calls.length, 0);

describe('LeadTable: printing the virtualized queue', () => {
  let container: HTMLDivElement;
  let root: Root;
  let fetchSpy: ReturnType<typeof vi.fn>;

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    scrollToOffset.length = 0;
    fetchSpy = vi.fn(() => Promise.reject(new Error('no network in this test')));
    vi.stubGlobal('fetch', fetchSpy);
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>
            <LeadTable leads={LEADS} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    vi.unstubAllGlobals();
  });

  it('renders every loaded row on beforeprint with no read, and re-windows on afterprint at the saved offset', () => {
    // happy-dom lays nothing out, so the window holds few rows (or none).
    expect(rowCount(container)).toBeLessThan(ROWS);
    const scroller = container.querySelector<HTMLDivElement>('.tbl-wrap')!;
    scroller.scrollTop = 480;
    expect(scroller.scrollTop).toBe(480);
    const callsBefore = apiCalls();

    act(() => {
      window.dispatchEvent(new Event('beforeprint'));
    });
    // flushSync: the rows are in the DOM before the print is laid out.
    expect(rowCount(container)).toBe(ROWS);
    expect(container.querySelector('.lead-table__virtual-spacer')).toBeNull();
    expect(container.querySelector(`tr[data-borrower-row="${LEADS[ROWS - 1].borrower_id}"]`)).not.toBeNull();

    act(() => {
      window.dispatchEvent(new Event('afterprint'));
    });
    expect(rowCount(container)).toBeLessThan(ROWS);
    expect(scrollToOffset).toContain(480);
    expect(apiCalls()).toBe(callsBefore);
    expect(fetchSpy).not.toHaveBeenCalled();
    // 600 rows mount synchronously in happy-dom: a heavy render on a loaded host.
  }, 30_000);

  it('stops listening once the table unmounts', () => {
    act(() => root.unmount());
    expect(() => window.dispatchEvent(new Event('beforeprint'))).not.toThrow();
    expect(scrollToOffset).toEqual([]);
    // The afterEach unmount is then a no-op on a fresh root.
    root = createRoot(container);
  });
});
