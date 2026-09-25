/**
 * @vitest-environment happy-dom
 *
 * The Lead Queue table's render cost (audit runtime-04, slices 1-3;
 * responsive-09 item 3), proven at the layer where it lived: the rendered
 * LeadTable, with the real hooks and the real virtualizer.
 *
 *   - Stable virtualizer inputs: getItemKey is a virtual-core memo
 *     dependency, so an inline one rebuilt every row measurement on every
 *     render. It keeps its identity across a cursor move; estimateSize
 *     follows the density (36 compact, 44 comfortable, plus the expanded
 *     preview's estimate); overscan is 5.
 *   - The sort runs once per (rows, sort): sortValue is not called again
 *     on a re-render that changes neither.
 */
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act, useState, type ReactNode } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { installLocalStorage } from '../../test/installLocalStorage';
import { clearSingleKeyShortcutsPreference } from '../../lib/keymapPreference';
import type { LeadTableSort } from './LeadTable.types';

(globalThis as unknown as { IS_REACT_ACT_ENVIRONMENT: boolean }).IS_REACT_ACT_ENVIRONMENT = true;

const app = vi.hoisted(() => ({ density: 'comfortable' as 'comfortable' | 'compact' }));
const counts = vi.hoisted(() => ({ sortValue: 0 }));
const virtualizerOptions = vi.hoisted(() => [] as Array<{
  getItemKey: (index: number) => string | number;
  estimateSize: (index: number) => number;
  overscan?: number;
  count: number;
}>);

vi.mock('../AppContext', () => ({
  useApp: () => ({
    approvals: {},
    setApproval: vi.fn(),
    setLastBorrowerId: vi.fn(),
    openConsoleRecentActivity: vi.fn(),
    saveLead: vi.fn(),
    isLeadSaved: () => false,
    setDrawer: vi.fn(),
    showEvidence: true,
    showConfidence: true,
    canApprove: true,
    actorEmail: 'approver.one@summit.example',
    sessionStatus: 'ready',
    density: app.density,
  }),
}));

vi.mock('../../lib/api', async (importOriginal) => ({
  ...(await importOriginal<typeof import('../../lib/api')>()),
  api: {
    draftOutreach: vi.fn(),
    approve: vi.fn(),
    reject: vi.fn(),
    campaign: vi.fn(),
    auditReceipt: () => new Promise(() => {}),
    salesTeam: () => Promise.resolve({ members: [] }),
  },
}));

vi.mock('@tanstack/react-virtual', async (importOriginal) => {
  const actual = await importOriginal<typeof import('@tanstack/react-virtual')>();
  return {
    ...actual,
    useVirtualizer: ((options: Parameters<typeof actual.useVirtualizer>[0]) => {
      virtualizerOptions.push(options as (typeof virtualizerOptions)[number]);
      return actual.useVirtualizer(options);
    }) as typeof actual.useVirtualizer,
  };
});

vi.mock('./LeadTable.logic', async (importOriginal) => {
  const actual = await importOriginal<typeof import('./LeadTable.logic')>();
  return {
    ...actual,
    sortValue: (...args: Parameters<typeof actual.sortValue>) => {
      counts.sortValue += 1;
      return actual.sortValue(...args);
    },
  };
});

import { LeadTable } from './LeadTable';

const IDS = ['B-RENDERCOST001', 'B-RENDERCOST002', 'B-RENDERCOST003', 'B-RENDERCOST004'];

function lead(borrowerId: string, index: number): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    display_name: `Owner ${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: 250000 + index * 1000,
    rate_spread_bps: 120,
    opportunity_score: 88 - index,
    confidence: 80,
    recommended_offer_code: 'refi',
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

const LEADS = IDS.map(lead);

let setTick: ((value: number) => void) | null = null;

/** Re-renders LeadTable on `setTick` with the same props (uncompiled, so the element is new). */
function Harness({ sort, expandedId = null }: { sort: LeadTableSort | null; expandedId?: string | null }) {
  'use no memo';

  const [tick, setTickState] = useState(0);
  setTick = setTickState;
  return (
    <div data-tick={tick}>
      <LeadTable
        leads={LEADS}
        sort={sort}
        onSortChange={() => undefined}
        expandedId={expandedId}
        onExpandedChange={() => undefined}
      />
    </div>
  );
}

describe('LeadTable render cost (runtime-04)', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  function render(node: ReactNode) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter initialEntries={['/lead-queue']}>{node}</MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  beforeEach(() => {
    installLocalStorage();
    clearSingleKeyShortcutsPreference();
    app.density = 'comfortable';
    counts.sortValue = 0;
    virtualizerOptions.length = 0;
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
    setTick = null;
  });

  const lastOptions = () => virtualizerOptions[virtualizerOptions.length - 1];

  it('keeps getItemKey stable across a cursor move, with overscan 5', () => {
    render(<Harness sort={null} />);
    const before = lastOptions().getItemKey;
    expect(before(0)).toBe(IDS[0]);
    const region = container.querySelector<HTMLDivElement>('.tbl-wrap')!;
    act(() => region.focus());
    act(() => {
      region.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true, cancelable: true }));
    });
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[0]);
    const calls = virtualizerOptions.length;
    act(() => {
      region.dispatchEvent(new KeyboardEvent('keydown', { key: 'j', bubbles: true, cancelable: true }));
    });
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(IDS[1]);
    expect(virtualizerOptions.length, 'precondition: the cursor move rendered the table').toBeGreaterThan(calls);
    expect(lastOptions().getItemKey).toBe(before);
    expect(lastOptions().overscan).toBe(5);
  });

  it('estimates 44px comfortable and 36px compact rows, plus the expanded preview', () => {
    render(<Harness sort={null} expandedId={IDS[1]} />);
    expect(lastOptions().estimateSize(0)).toBe(44);
    expect(lastOptions().estimateSize(1)).toBe(44 + 520);

    app.density = 'compact';
    act(() => setTick?.(1));
    expect(lastOptions().estimateSize(0)).toBe(36);
    expect(lastOptions().estimateSize(1)).toBe(36 + 520);
  });

  it('sorts once per rows and sort: a re-render with neither changed calls sortValue again zero times', () => {
    render(<Harness sort={{ key: 'equity', dir: 'desc' }} />);
    expect(counts.sortValue, 'precondition: the equity sort ran').toBeGreaterThan(0);
    const firstRow = container.querySelector('tr[data-borrower-row]')?.getAttribute('data-borrower-row');
    expect(firstRow).toBe(IDS[3]);
    const afterSort = counts.sortValue;

    act(() => setTick?.(1));
    act(() => setTick?.(2));

    expect(counts.sortValue).toBe(afterSort);
  });
});
