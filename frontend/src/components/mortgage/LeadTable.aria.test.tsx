/**
 * @vitest-environment happy-dom
 *
 * aria-sort contract for the lead table (re-audit 2026-06-11: the
 * remediation wired `aria-sort` onto the <th> columnheaders — the only
 * role the ARIA spec defines it for — but shipped no committed
 * assertion; the claim was browser-probe only). This renders the real
 * LeadTable and pins:
 *
 * 1. every sortable header carries aria-sort: 5 in the Default view (Equity,
 *    Rate, Score, Signal and the merged Status column, whose menu keeps the
 *    relationship / assignee / outreach keys), 7 in Sales ops (the four
 *    numeric headers plus Relationship, Assigned to and Outreach);
 * 2. the idle state is "none" everywhere (default sort is rank, which
 *    is not one of the sortable headers);
 * 3. activating a header flips it to descending, then ascending, while
 *    every other header stays "none".
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
  api: {},
  ApiError: class extends Error {},
  isAbortError: () => false,
}));

function lead(borrowerId: string, score: number): LeadSummary {
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
    opportunity_score: score,
    confidence: 80,
    recommended_offer: 'Refinance',
    why_now: 'test',
    evidence_ids: ['ev-1'],
    approval_status: 'pending',
  } as unknown as LeadSummary;
}

describe('LeadTable aria-sort columnheaders', () => {
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

  function mount(view: 'default' | 'sales-ops' = 'default') {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={[lead('B-AAAAAAAAAAAA1', 90), lead('B-AAAAAAAAAAAA2', 70)]} view={view} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });
  }

  it('exposes aria-sort on every sortable header, idle state none', () => {
    mount();
    const headers = Array.from(container.querySelectorAll('th[aria-sort]'));
    expect(headers).toHaveLength(5);
    expect(headers.map((th) => th.getAttribute('aria-sort'))).toEqual(
      Array(5).fill('none'),
    );
  });

  it('keeps the three workflow sort keys on the Sales ops headers', () => {
    mount('sales-ops');
    const headers = Array.from(container.querySelectorAll('th[aria-sort]'));
    expect(headers).toHaveLength(7);
    expect(headers.map((th) => th.querySelector('button')?.getAttribute('aria-label'))).toEqual([
      'Sort by Relationship',
      'Sort by Assigned to',
      'Sort by Outreach',
      'Sort by Equity',
      'Sort by Rate Δ (bps)',
      'Sort by Score',
      'Sort by Signal',
    ]);
    expect(container.querySelector('[data-testid="lead-status-sort"]')).toBeNull();
  });

  it('sorts by a workflow key from the Status header menu and reports it on that th', () => {
    mount();
    const trigger = container.querySelector<HTMLButtonElement>('[data-testid="lead-status-sort"]');
    expect(trigger?.getAttribute('aria-haspopup')).toBe('menu');
    act(() => trigger!.click());
    const items = Array.from(container.querySelectorAll<HTMLButtonElement>('[role="menu"] [role="menuitemradio"]'));
    expect(items.map((item) => item.textContent)).toEqual(['Relationship', 'Assigned to', 'Outreach']);
    act(() => items[2].click());

    const statusTh = trigger!.closest('th');
    expect(container.querySelector('[role="menu"]')).toBeNull();
    expect(statusTh?.getAttribute('aria-sort')).toBe('descending');
    expect(trigger!.getAttribute('aria-label')).toBe('Status, sorted by Outreach. Sort options');
    expect(container.querySelector('[data-testid="lead-sort-scope"]')?.textContent).toContain('sorted within the loaded 2');

    act(() => trigger!.click());
    const checked = container.querySelector('[role="menuitemradio"][aria-checked="true"]');
    expect(checked?.textContent).toBe('Outreach');
    act(() => (checked as HTMLButtonElement).click());
    expect(statusTh?.getAttribute('aria-sort')).toBe('ascending');
  });

  it('activating Score flips its header through descending then ascending', () => {
    mount();
    const scoreButton = Array.from(
      container.querySelectorAll('th[aria-sort] button'),
    ).find((btn) => btn.getAttribute('aria-label') === 'Sort by Score') as HTMLButtonElement;
    expect(scoreButton).toBeTruthy();

    act(() => scoreButton.click());
    const scoreTh = () => scoreButton.closest('th');
    expect(scoreTh()?.getAttribute('aria-sort')).toBe('descending');

    act(() => scoreButton.click());
    expect(scoreTh()?.getAttribute('aria-sort')).toBe('ascending');

    const others = Array.from(container.querySelectorAll('th[aria-sort]')).filter(
      (th) => th !== scoreTh(),
    );
    expect(others.map((th) => th.getAttribute('aria-sort'))).toEqual(
      Array(4).fill('none'),
    );
  });

  it('counts the expanded preview row and shifts later row indices', () => {
    const queryClient = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    act(() => {
      root.render(
        <QueryClientProvider client={queryClient}>
          <MemoryRouter>
            <LeadTable leads={[
              lead('B-AAAAAAAAAAAA1', 90),
              lead('B-AAAAAAAAAAAA2', 80),
              lead('B-AAAAAAAAAAAA3', 70),
            ]} />
          </MemoryRouter>
        </QueryClientProvider>,
      );
    });

    const table = container.querySelector('table');
    expect(table?.getAttribute('aria-rowcount')).toBe('4');
    expect(Array.from(container.querySelectorAll('tbody tr[aria-rowindex]')).map(
      (row) => row.getAttribute('aria-rowindex'),
    )).toEqual(['2', '3', '4']);

    const borrowerButtons = container.querySelectorAll<HTMLButtonElement>(
      '.lead-table__borrower-btn',
    );
    act(() => borrowerButtons[1].click());

    expect(table?.getAttribute('aria-rowcount')).toBe('5');
    expect(Array.from(container.querySelectorAll('tbody tr[aria-rowindex]')).map(
      (row) => row.getAttribute('aria-rowindex'),
    )).toEqual(['2', '3', '4', '5']);
    expect(container.querySelector('.tbl__expand')?.getAttribute('aria-rowindex')).toBe('4');
  });
});
