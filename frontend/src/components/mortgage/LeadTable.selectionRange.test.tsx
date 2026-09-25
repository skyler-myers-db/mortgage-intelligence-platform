/**
 * @vitest-environment happy-dom
 *
 * Bulk selection on the rendered table (audit tables-07):
 *
 *  - Shift-click on a row checkbox selects from the anchor (the last plain
 *    toggle) to the clicked row, in ON-SCREEN order (after a sort), skipping
 *    rows that may not be selected; Shift+X does the same to the cursor row.
 *  - The selection the table acts on is the stored selection intersected
 *    with the rows on screen: after the rows change, the toolbar count, the
 *    header checkbox, the CSV scope and the Cmd-K counts all drop the rows
 *    that left.
 *  - A row whose decision is on the wire is not approval-eligible.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { LeadSummary } from '../../types';
import { currentCommandSelection } from '../command/commandSelection';

const approve = vi.fn();

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
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {
    approve: (...args: unknown[]) => approve(...args),
    draftOutreach: () => new Promise(() => undefined),
  },
  ApiError: class extends Error {},
  isAbortError: () => false,
  isWarmingUpError: () => false,
}));

import { LeadTable } from './LeadTable';

function lead(n: number, equity: number, status: 'pending' | 'rejected' = 'pending'): LeadSummary {
  return {
    borrower_id: `B-AAAAAAAAAAAA${n}`,
    clip: `clip_${n}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: equity,
    rate_spread_bps: 120,
    opportunity_score: 90 - n,
    confidence: 80,
    recommended_offer: 'Refinance',
    evidence_ids: ['ev-1'],
    approval_status: status,
    marketing_eligible: true,
  } as unknown as LeadSummary;
}

// Rank order 1..6; equity order (desc) 6, 5, 4, 3, 2, 1. Row 4 is rejected.
const ROWS = [
  lead(1, 100_000),
  lead(2, 200_000),
  lead(3, 300_000),
  lead(4, 400_000, 'rejected'),
  lead(5, 500_000),
  lead(6, 600_000),
];
const id = (n: number) => `B-AAAAAAAAAAAA${n}`;

describe('LeadTable range selection and the on-screen selection', () => {
  let container: HTMLDivElement;
  let root: Root;
  let queryClient: QueryClient;

  beforeEach(() => {
    vi.clearAllMocks();
    queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function render(leads: LeadSummary[]) {
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={leads} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  const checkbox = (n: number) => container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${id(n)}"]`)!;
  const click = (n: number, shiftKey = false) => {
    act(() => {
      checkbox(n).dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, shiftKey }));
    });
  };
  const selected = () => ROWS.map((row) => row.borrower_id).filter((borrowerId) => (
    container.querySelector<HTMLInputElement>(`[data-testid="lead-select-${borrowerId}"]`)?.checked
  ));
  const toolbarLabel = () => container.querySelector('.bulk-actions__label')?.textContent ?? '';
  const region = () => container.querySelector<HTMLElement>('.tbl-wrap')!;
  const press = (key: string, shiftKey = false) => {
    act(() => {
      region().dispatchEvent(new KeyboardEvent('keydown', { key, shiftKey, bubbles: true, cancelable: true }));
    });
  };

  it('Shift-click selects anchor to target in on-screen order and skips rows that may not be selected', () => {
    render(ROWS);
    act(() => container.querySelector<HTMLButtonElement>('button[aria-label="Sort by Equity"]')!.click());
    // On screen: 6, 5, 4 (rejected), 3, 2, 1.
    click(5);
    click(2, true);

    expect(selected()).toEqual([id(2), id(3), id(5)]);
    expect(toolbarLabel()).toBe('3 leads selected');
  });

  it('Shift+X extends from the anchor to the cursor row', () => {
    render(ROWS);
    click(2);
    region().focus();
    // Cursor: J lands on row 1, then walks to row 6 (rank order on screen).
    for (let step = 0; step < 6; step += 1) press('j');
    expect(container.querySelector('tr.is-cursor')?.getAttribute('data-borrower-row')).toBe(id(6));

    press('X', true);

    expect(selected()).toEqual([id(2), id(3), id(5), id(6)]);
  });

  it('counts, the header checkbox, the CSV scope and Cmd-K read only the rows on screen', () => {
    render(ROWS);
    click(1);
    click(2);
    click(3);
    expect(toolbarLabel()).toBe('3 leads selected');

    // A filter change: rows 1 and 2 leave the screen.
    render(ROWS.slice(2));

    expect(toolbarLabel()).toBe('1 lead selected');
    const header = container.querySelector<HTMLInputElement>('[data-testid="lead-select-all"]')!;
    expect(header.checked).toBe(false);
    expect(header.indeterminate).toBe(true);
    expect(container.querySelector('[data-testid="lead-export"]')?.textContent).toBe('Export 1 selected');
    expect(currentCommandSelection()).toMatchObject({ selectedCount: 1, approveCount: 1 });
    expect(container.querySelector('[data-testid="lead-bulk-approve"]')?.textContent).toBe('Approve 1 eligible');
  });

  it('no toolbar for a selection that is entirely off screen', () => {
    render(ROWS);
    click(1);
    render(ROWS.slice(2));

    expect(container.querySelector('[data-testid="lead-bulk-actions"]')).toBeNull();
    expect(currentCommandSelection()).toBeNull();
  });
});
