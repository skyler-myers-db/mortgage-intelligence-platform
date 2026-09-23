/**
 * @vitest-environment happy-dom
 *
 * Sort-scope disclosure (audit tables-02, S slice, 2026-09-21).
 *
 * /api/leads returns the top-ranked window (500 rows by default) and the
 * table sorts that array client-side. Sorting by Equity therefore reorders
 * only the loaded rows, not the thousands that match — and nothing said so.
 * `toggleSort('rank')` existed but no header reached it, so there was no way
 * back to rank order either. Pins, on the rendered table:
 *
 * 1. rank order (the default) makes no sort-scope claim and shows no reset;
 * 2. any other sort key states "sorted within the loaded <n>", naming the
 *    larger matching total when the window is truncated;
 * 3. "Reset to rank" restores the server's rank order and clears the claim.
 */

import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { act } from 'react';
import { createRoot, type Root } from 'react-dom/client';
import { MemoryRouter } from 'react-router';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { LeadTable } from './LeadTable';
import type { LeadSummary } from '../../types';

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
    actorEmail: null,
    sessionStatus: 'ready',
  }),
}));

vi.mock('../../lib/api', () => ({
  api: {},
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

function lead(borrowerId: string, equity: number): LeadSummary {
  return {
    borrower_id: borrowerId,
    clip: `clip_${borrowerId}`,
    city: 'Chicago',
    state: 'IL',
    zip: '60611',
    segment_codes: ['itm'],
    equity_estimate: equity,
    rate_spread_bps: 120,
    opportunity_score: 80,
    confidence: 80,
    recommended_offer: 'Refinance',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
    marketing_eligible: true,
  } as unknown as LeadSummary;
}

// Server rank order: 1, 2, 3. Equity order (desc): 2, 3, 1.
const RANKED = [
  lead('B-AAAAAAAAAAAA1', 100_000),
  lead('B-AAAAAAAAAAAA2', 900_000),
  lead('B-AAAAAAAAAAAA3', 400_000),
];

describe('LeadTable sort-scope disclosure', () => {
  let container: HTMLDivElement;
  let root: Root;

  beforeEach(() => {
    container = document.createElement('div');
    document.body.appendChild(container);
    root = createRoot(container);
  });

  afterEach(() => {
    act(() => root.unmount());
    container.remove();
  });

  function mount(totalMatching: number | null) {
    const queryClient = new QueryClient({ defaultOptions: { queries: { retry: false } } });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={RANKED} totalMatching={totalMatching} truncatedAt={totalMatching ? 3 : null} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  const rowOrder = () => Array.from(
    container.querySelectorAll('.lead-table__borrower'),
  ).map((node) => node.textContent);
  const footer = () => container.querySelector('.surface__ft')?.textContent ?? '';
  const scope = () => container.querySelector('[data-testid="lead-sort-scope"]');
  const reset = () => container.querySelector<HTMLButtonElement>('[data-testid="lead-sort-reset"]');
  const sortBy = (label: string) => {
    const button = container.querySelector<HTMLButtonElement>(`button[aria-label="Sort by ${label}"]`);
    if (!button) throw new Error(`sort header not rendered: ${label}`);
    act(() => button.click());
  };

  it('makes no sort-scope claim and offers no reset in rank order', () => {
    mount(1_200);

    expect(rowOrder()).toEqual(['B-AAAAAAAAAAAA1', 'B-AAAAAAAAAAAA2', 'B-AAAAAAAAAAAA3']);
    expect(scope()).toBeNull();
    expect(reset()).toBeNull();
    expect(footer()).toContain('Showing 3 ranked borrowers of 1,200 total matching filters');
  });

  it('says the sort covers only the loaded rows when the window is truncated', () => {
    mount(1_200);
    sortBy('Equity');

    expect(rowOrder()).toEqual(['B-AAAAAAAAAAAA2', 'B-AAAAAAAAAAAA3', 'B-AAAAAAAAAAAA1']);
    expect(footer()).toContain('sorted within the loaded 3, not across all 1,200 matching');
    expect(reset()?.textContent).toBe('Reset to rank');
  });

  it('keeps the claim exact when every matching row is loaded', () => {
    mount(null);
    sortBy('Equity');

    expect(scope()?.textContent).toContain('sorted within the loaded 3');
    expect(scope()?.textContent).not.toContain('not across');
  });

  it('"Reset to rank" restores the server rank order and clears the claim', () => {
    mount(1_200);
    sortBy('Equity');
    sortBy('Equity'); // ascending: 1, 3, 2 — still not rank order
    expect(rowOrder()).toEqual(['B-AAAAAAAAAAAA1', 'B-AAAAAAAAAAAA3', 'B-AAAAAAAAAAAA2']);

    act(() => reset()!.click());

    expect(rowOrder()).toEqual(['B-AAAAAAAAAAAA1', 'B-AAAAAAAAAAAA2', 'B-AAAAAAAAAAAA3']);
    expect(scope()).toBeNull();
    expect(reset()).toBeNull();
    expect(
      Array.from(container.querySelectorAll('th[aria-sort]')).map((th) => th.getAttribute('aria-sort')),
    ).toEqual(Array(5).fill('none'));
  });
});
